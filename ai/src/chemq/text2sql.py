"""W2 交付物：QueryPlan -> SQL -> 执行结果。

验收标准（domains/<领域>/eval/golden.jsonl）：
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

from dataclasses import dataclass

from .config import settings
from .db import run_query
from .dialects import DIALECT_PROFILE, profile  # noqa: F401  —— DIALECT_PROFILE 是再导出
from .domain import domain
from .llm import Llm, LlmResult
from .prompts import render
from .router import load_metric_dictionary
from .schemas import QueryPlan, SqlCandidate
from .sqlguard import check

# 提示词正文在 prompts/<locale>/text2sql.md。两类空位：
#   领域相关（公司名、可过滤维度……）→ domains/<领域>/domain.yaml 的 prompt_slots
#   方言相关（引擎名、月份写法）      → chemq/dialects.py 的 DIALECT_PROFILE
# DIALECT_PROFILE 在这里再导出一次：tests/test_dialect.py 和 solutions/ 从 text2sql 引用它。


def system_role(dialect: str | None = None) -> str:
    """按方言渲染 system prompt。留空则取 settings().sql_dialect。"""
    return render("text2sql", extra=profile(dialect or settings().sql_dialect))


# 兼容旧引用（solutions/text2sql_w2.py 直接 import 了它）。
# 注意：这是**渲染时固定成 postgres** 的常量，换方言要调 system_role()。
SYSTEM_ROLE = system_role("postgres")


def schema_context(dialect: str | None = None) -> str:
    """给模型看的「语义层目录」：能查哪些视图/维度表、有哪些列、维度有哪些取值。

    由 scripts/build_catalog.py 从数据库生成（make catalog / make seed），提交进仓库。
    以前这里直接返回 03_semantic_views.sql 原文 —— 视图定义里的 `FROM fact_sales`、
    `received_cny` 被模型学去，查明细表、用英文底层列名（W3 主评测 Q004）。

    路径解析留了换引擎的口子：领域包里 <dialect>/semantic_catalog.md 存在就用它，
    否则回落到平铺的 semantic_catalog.md（只对 postgres）。
    """
    dialect = dialect or settings().sql_dialect
    d = domain()

    per_dialect = d.catalog_path_for(dialect)
    if per_dialect.exists():
        return per_dialect.read_text(encoding="utf-8")

    flat = d.catalog_path
    if dialect == "postgres":
        if flat.exists():
            return flat.read_text(encoding="utf-8")
        raise FileNotFoundError(f"缺少语义层目录 {flat}。先跑 make catalog（需要数据库已 make seed）")

    raise NotImplementedError(
        f"缺少 {dialect} 的语义层目录：{per_dialect} 不存在。"
        f"Doris 的语义层是 W5 交付物，见 docs/weekly/w5.md。"
    )


class Unanswerable(PermissionError):
    """text2sql 判定「这个问题用语义层答不了」。

    拒答不是异常 —— 它是一个合法的答案（"查不到"），pipeline 要把它变成 refused=True，
    进评测分母、计成本。用异常传递只是因为它要中断"生成 → 执行"这条路径。

    继承 PermissionError：之前护栏拦截抛的就是 PermissionError，已有的 except 不用改。
    带 cost_usd：模型拒答、被护栏拦下，Sonnet 都是真调用过的。不带出来，成本就又漏了。
    """

    def __init__(self, reason: str, *, kind: str, cost_usd: float = 0.0, sql: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind            # model_refused / guard_rejected / parse_failed
        self.cost_usd = cost_usd
        self.sql = sql


@dataclass
class SqlRun:
    """一次成功取数的全部产出。用对象而不是元组：字段名就是文档，加字段不会打破调用方的解包。"""

    sql: str
    rows: list[dict]
    candidate: SqlCandidate
    cost_usd: float


def build_user_message(plan: QueryPlan, *, question: str = "", as_of: str | None = None) -> str:
    """text2sql 收到的 user 消息。

    之前只传 plan.model_dump_json() —— 而 QueryPlan 里没有原始问题。
    「最低」「两个」「前三」这些决定排序方向和 LIMIT 的词，在 router 那一步就丢了，
    text2sql 只能从 reasoning 里碰运气。prompt 第 7~10 条写得再清楚，它也看不到要套用规则的那个词。

    time_range 由 router 算好，这里明确说以它为准，免得 text2sql 根据原话再算一遍、两层算出不同的日期。
    """
    parts: list[str] = []
    if question:
        parts.append(f"用户原始问题：{question}")
    if as_of:
        parts.append(f"基准时间：{as_of}")
    parts.append("查询计划（上游解析器给出；如果有 time_range，以它为准，不要根据原始问题重新推算日期）：")
    parts.append(plan.model_dump_json())
    return "\n".join(parts)


def generate(
    plan: QueryPlan,
    llm: Llm | None = None,
    *,
    question: str = "",
    as_of: str | None = None,
) -> LlmResult:
    """返回 LlmResult 而不只是 SqlCandidate —— 调用方需要 usage 来记成本。"""
    llm = llm or Llm()
    system = Llm.cached_system(
        system_role(),
        schema_context(),
        "# 指标口径字典\n" + load_metric_dictionary(),
    )
    return llm.parse(
        model=settings().model_sql,
        system=system,
        user=build_user_message(plan, question=question, as_of=as_of),
        schema=SqlCandidate,
    )


def generate_and_run(
    plan: QueryPlan,
    region: str | None = None,
    *,
    question: str = "",
    as_of: str | None = None,
    llm: Llm | None = None,
) -> SqlRun:
    """生成 → 护栏 → 执行。答不了时抛 Unanswerable（带成本），不返回空结果。

    不返回空结果是刻意的：空 rows 和"查不到"在用户眼里是两回事，
    前者会被读成"数是 0"。
    """
    res = generate(plan, llm, question=question, as_of=as_of)
    cost = res.usage.cost_usd
    candidate: SqlCandidate | None = res.parsed

    if candidate is None:
        raise Unanswerable("模型没有返回可解析的结构化结果", kind="parse_failed", cost_usd=cost)

    if candidate.refuse_reason:
        raise Unanswerable(candidate.refuse_reason, kind="model_refused", cost_usd=cost)

    verdict = check(candidate.sql, settings().allowed_relations, settings().max_rows)
    # ok=True 时 rewritten_sql 一定非 None，但这是 sqlguard 的"约定"，不是类型系统能看见的事实。
    if not verdict.ok or verdict.rewritten_sql is None:
        raise Unanswerable(
            "SQL 未通过护栏: " + "; ".join(verdict.violations),
            kind="guard_rejected", cost_usd=cost, sql=candidate.sql,
        )
    sql = verdict.rewritten_sql
    return SqlRun(sql=sql, rows=run_query(sql, region=region), candidate=candidate, cost_usd=cost)
