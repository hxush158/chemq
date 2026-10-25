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
from .domain import domain
from .llm import Llm, LlmResult
from .router import load_metric_dictionary
from .schemas import QueryPlan, SqlCandidate
from .sqlguard import check

# SYSTEM_ROLE 里只有两处是方言相关的：引擎名，和月份的格式化函数。
# 抽成模板 + 一张方言表，而不是维护两份 prompt —— 两份 prompt 一定会漂移。
# W2 那四条输出契约（第 6-9 条）当初选择写进 prompt 而不是写进 evals 的
# rows_match 归一化逻辑，回报就在这里：它们现在是可按方言替换的参数，
# 而不是评测代码里的 if dialect == ...
_SYSTEM_ROLE_TMPL = """你是 {ENGINE} 专家，为示例化工经营分析系统生成只读 SQL。

硬性约束：
1. 只能 SELECT，且只能查「语义层目录」里列出的视图和维度表。目录里没有的表和列都不存在，
   fact_* 明细表一律不可访问，也不要猜英文底层列名。
2. 指标口径必须引用指标字典；字典里没有的指标一律不要造。
3. 不要在 SQL 里硬编码折标煤系数、税率等参数，从维度表取。
4. 同比/环比必须同时返回两期原值。
5. 结果列名用中文，与指标字典一致，便于人工复核。
输出约定（结果集要能直接给人看，也要能被程序比对）：
6. 只输出问题真正要的列，不要附加没被问到的维度。
   结果里**如果有**月份/年月列，用 {MONTH_FMT} 格式输出，不要返回 date 或 timestamp；
   但问题没有要求按月份分组时（例如只查某一个月），不要自己加月份列，
   也不要把它放进 GROUP BY。
7. 问「哪个/谁…最高/最低/最多/最少」→ 排序后 LIMIT 1，只返回那一行。
8. 问「前 N 个 / 排名前 N / 最…的 N 个」→ 排序后 LIMIT N，只返回 N 行。
9. 排序方向由问题里的方向词决定，不要默认降序：
     最低 / 最少 / 最小 / 最差 / 垫底  →  ORDER BY <该指标> ASC
     最高 / 最多 / 最大 / 最好 / 领先  →  ORDER BY <该指标> DESC
   「最低的两个」= ORDER BY ... ASC LIMIT 2，不是 DESC。
10. 不要把整张排名表甩给用户 —— 问一个就给一行，问 N 个就给 N 行。
11. 「最近 N 个月」以基准时间所在的月份为最后一个月，一共 N 个月
    （基准 2026-09，最近 6 个月 = 2026-04 ~ 2026-09）。
12. 环比/同比要返回两期的原值各一行，不要压成一行再加一个差值列。
13. 用语义层视图答不了时（指标不存在、所需时间段没有数据），把原因写进 refuse_reason、sql 留空。
    绝不要把拒绝写成 SELECT '…' 字面量 —— 那会被当成一条正常结果返回给用户。
14. 按名称过滤（装置、产品、客户、大区、能源）只能用目录「维度取值」里的原值，一字不差，
    或者改用对应的 ID 列；不要缩写或改写名称，不要用 LIKE 模糊匹配。
15. 问「是多少 / 有多少 / 多高」这类，按问题的时间范围汇总成结果；
    只有明确要求「按天 / 逐日 / 每天 / 列出明细」时才返回明细行。
16. 问题里同时问了「为什么 / 什么原因 / 是不是因为」时，原因由知识库回答，不归 SQL 管：
    SQL 只回答「是多少」那部分，不要为了解释原因额外加明细行或分组（如按装置拆开）。
    但问题里**点名的对比期**属于「是多少」：问「X 月为什么比 Y 月高」，X、Y 两期都要返回（同规则 4、12）。
"""

DIALECT_PROFILE: dict[str, dict[str, str]] = {
    "postgres": {
        "ENGINE": "PostgreSQL 16",
        "MONTH_FMT": "to_char(<日期列>, 'YYYY-MM')",
    },
    # ⚠️ W5 接真实 Doris 时要验证：date_format 的格式串是 MySQL 系的 %Y-%m，
    #    to_char 在 Doris 上大概率不存在。验证前不要当成已确认的事实。
    "doris": {
        "ENGINE": "Apache Doris 4.0",
        "MONTH_FMT": "date_format(<日期列>, '%Y-%m')",
    },
}


def system_role(dialect: str | None = None) -> str:
    """按方言渲染 system prompt。留空则取 settings().sql_dialect。"""
    dialect = dialect or settings().sql_dialect
    try:
        return _SYSTEM_ROLE_TMPL.format(**DIALECT_PROFILE[dialect])
    except KeyError:
        raise ValueError(
            f"没有 {dialect!r} 的 prompt 方言配置，可选：{sorted(DIALECT_PROFILE)}"
        ) from None


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
