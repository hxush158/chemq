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

# SYSTEM_ROLE 里只有两处是方言相关的：引擎名，和月份的格式化函数。
# 抽成模板 + 一张方言表，而不是维护两份 prompt —— 两份 prompt 一定会漂移。
# W2 那四条输出契约（第 6-9 条）当初选择写进 prompt 而不是写进 evals 的
# rows_match 归一化逻辑，回报就在这里：它们现在是可按方言替换的参数，
# 而不是评测代码里的 if dialect == ...
_SYSTEM_ROLE_TMPL = """你是 {ENGINE} 专家，为示例化工经营分析系统生成只读 SQL。

硬性约束：
1. 只能 SELECT，且只能查下列语义层视图，禁止查 fact_* / dim_* 明细表之外的任何对象。
2. 指标口径必须引用指标字典；字典里没有的指标一律不要造。
3. 不要在 SQL 里硬编码折标煤系数、税率等参数，从维度表取。
4. 同比/环比必须同时返回两期原值。
5. 结果列名用中文，与指标字典一致，便于人工复核。
输出约定（结果集要能直接给人看，也要能被程序比对）：
6. 月份/年月一律用 {MONTH_FMT} 输出，不要返回 date 或 timestamp。
7. 问「哪个/谁…最高/最低/最多/最少」时，按该指标排序后 LIMIT 1，只返回那一行；
   问「前 N 个」才返回 N 行。不要把整张排名表甩给用户。
8. 「最近 N 个月」以基准时间所在的月份为最后一个月，一共 N 个月
   （基准 2026-09，最近 6 个月 = 2026-04 ~ 2026-09）。
9. 环比/同比要返回两期的原值各一行，不要压成一行再加一个差值列。
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
    """TODO(W2): 从 db 读 information_schema + COMMENT 动态生成，
    先手写一版，跑通后再改成自动生成（生产系统必须自动生成，否则 schema 漂移）。

    路径解析留了 W5 的口子：db/<dialect>/03_semantic_views.sql 存在就用它，
    否则回落到现在平铺的 db/03_semantic_views.sql。
    W5 真把 db/ 拆成 db/postgres/ + db/doris/ 时，这个函数一行都不用改。
    """
    dialect = dialect or settings().sql_dialect
    root = Path(__file__).resolve().parents[3] / "db"

    per_dialect = root / dialect / "03_semantic_views.sql"
    if per_dialect.exists():
        return per_dialect.read_text(encoding="utf-8")

    flat = root / "03_semantic_views.sql"
    if dialect == "postgres" and flat.exists():
        return flat.read_text(encoding="utf-8")

    raise NotImplementedError(
        f"缺少 {dialect} 的语义层定义：{per_dialect} 不存在。"
        f"Doris 的语义层视图是 W5 交付物，见 docs/weekly/w5.md。"
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
    # ok=True 时 rewritten_sql 一定非 None，但这是 sqlguard 的"约定"，不是类型系统能看见的事实。
    # 所以显式判空：既让类型检查器把 sql 收窄成 str，也防住哪天 sqlguard 改坏了约定。
    if not verdict.ok or verdict.rewritten_sql is None:
        raise PermissionError("SQL 未通过护栏: " + "; ".join(verdict.violations))
    sql = verdict.rewritten_sql
    return sql, run_query(sql, region=region)
