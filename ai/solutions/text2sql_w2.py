"""【参考实现 · W2】Text-to-SQL 的完整版。

看之前先回答：
  1. schema 应该整份注入，还是先做 schema linking 只注入相关表？各自的失败模式是什么？
  2. 生成的 SQL 被 sqlguard 拒了，应该重试几次？重试时要把什么信息喂回去？
  3. 为什么要求模型同时返回 metric_definitions_used？它不参与执行，为什么还要它？
"""
from __future__ import annotations

import structlog

from chemq.config import settings
from chemq.db import run_query
from chemq.llm import Llm
from chemq.router import load_metric_dictionary
from chemq.schemas import QueryPlan, SqlCandidate
from chemq.sqlguard import check
from chemq.text2sql import SYSTEM_ROLE, schema_context

log = structlog.get_logger()
#MAX_REPAIR = 1
MAX_REPAIR = 3


def generate_and_run(plan: QueryPlan, region: str | None = None) -> tuple[str, list[dict], SqlCandidate]:
    llm = Llm()
    # 决定 1：W2 整份注入（只有 4 个视图）。
    # schema linking 的失败模式是"该给的表没给"，症状是模型编列名，很难 debug；
    # 整份注入的失败模式是"上下文太长成本高"，症状明显且可测量。
    # 先选失败模式好定位的那个，等视图涨到几十个再上检索。
    system = Llm.cached_system(
        SYSTEM_ROLE,
        "# 语义层定义\n```sql\n" + schema_context() + "\n```",
        "# 指标口径字典\n" + load_metric_dictionary(),
    )

    user = plan.model_dump_json()
    last_violations: list[str] = []

    for attempt in range(MAX_REPAIR + 1):
        candidate: SqlCandidate = llm.parse(
            model=settings().model_sql, system=system, user=user, schema=SqlCandidate
        ).parsed
        verdict = check(candidate.sql, settings().allowed_relations, settings().max_rows)

        if verdict.ok and verdict.rewritten_sql is not None:
            sql = verdict.rewritten_sql          # 收窄成 str，见 sqlguard.py 顶部的 SqlNode 注释
            rows = run_query(sql, region=region)
            log.info("sql_ok", attempt=attempt, rows=len(rows),
                     metrics=candidate.metric_definitions_used)
            return sql, rows, candidate

        last_violations = verdict.violations
        log.warning("sql_rejected", attempt=attempt, violations=verdict.violations)

        # 决定 2：只重试一次，且必须把具体的 violations 喂回去。
        # 不带原因的重试等于换个随机种子再抽一次，白花钱。
        # 重试超过 1 次通常说明 prompt 有系统性问题（比如没告诉模型明细表不可查），
        # 应该去改 prompt，而不是靠重试次数硬堆。
        user = (
            f"{plan.model_dump_json()}\n\n"
            f"# 上一版 SQL 被安全护栏拒绝\n"
            f"```sql\n{candidate.sql}\n```\n"
            f"拒绝原因：\n- " + "\n- ".join(verdict.violations) +
            "\n请修正后重新生成，不要重复同样的问题。"
        )

    raise PermissionError("SQL 两次均未通过护栏: " + "; ".join(last_violations))


# 决定 3：metric_definitions_used 不参与执行，它是"可复核性"的载体。
# 人工复核时，审核的人不需要读懂 SQL，只要看模型声称用了哪条口径，
# 再对一眼结果合不合理就行。企业里能不能上线，往往卡在"业务方能不能自己验"这一关。
