"""W2 交付物：QueryPlan -> SQL -> 执行结果。

验收标准（evals/golden.jsonl）：
  · 执行准确率(Execution Accuracy) ≥ 80%   —— 结果集与标准答案一致
  · 口径正确率 ≥ 95%                        —— 用对了字典里的指标定义
  · 被 sqlguard 拦下的 SQL 占比 < 5%        —— 拦太多说明 prompt 没写好
  · 恶意/越权 SQL 拦截率 = 100%

TODO(W2):
  1. 把语义层视图的 DDL + COMMENT 注入 system（schema linking 的最简版本）
  2. 生成 SqlCandidate（structured outputs），再过 sqlguard.check
  3. 被拦下时带着 violations 重试一次，仍失败则拒答，不要静默降级
  4. 执行走 db.run_query（只读账号 + RLS region）
"""
from __future__ import annotations

from pathlib import Path

from .config import settings
from .db import run_query
from .llm import Llm
from .router import load_metric_dictionary
from .schemas import QueryPlan, SqlCandidate
from .sqlguard import check

SYSTEM_ROLE = """你是 PostgreSQL 16 专家，为示例化工经营分析系统生成只读 SQL。

硬性约束：
1. 只能 SELECT，且只能查下列语义层视图，禁止查 fact_* / dim_* 明细表之外的任何对象。
2. 指标口径必须引用指标字典；字典里没有的指标一律不要造。
3. 不要在 SQL 里硬编码折标煤系数、税率等参数，从维度表取。
4. 同比/环比必须同时返回两期原值。
5. 结果列名用中文，与指标字典一致，便于人工复核。
"""


def schema_context() -> str:
    """TODO(W2): 从 db 读 information_schema + COMMENT 动态生成，
    先手写一版，跑通后再改成自动生成（生产系统必须自动生成，否则 schema 漂移）。"""
    return (Path(__file__).resolve().parents[3] / "db" / "03_semantic_views.sql").read_text(
        encoding="utf-8"
    )


def generate(plan: QueryPlan, llm: Llm | None = None) -> SqlCandidate:
    llm = llm or Llm()
    system = Llm.cached_system(
        SYSTEM_ROLE,
        "# 语义层定义\n```sql\n" + schema_context() + "\n```",
        "# 指标口径字典\n" + load_metric_dictionary(),
    )
    res = llm.parse(
        model=settings().model_sql,
        system=system,
        user=plan.model_dump_json(),
        schema=SqlCandidate,
    )
    return res.parsed


def generate_and_run(plan: QueryPlan, region: str | None = None) -> tuple[str, list[dict]]:
    candidate = generate(plan)
    verdict = check(candidate.sql, settings().allowed_relations, settings().max_rows)
    if not verdict.ok:
        raise PermissionError("SQL 未通过护栏: " + "; ".join(verdict.violations))
    return verdict.rewritten_sql, run_query(verdict.rewritten_sql, region=region)
