"""schema_context—— 智能问数最重要、也最容易被跳过的一层。

设计要点（面试可直接讲）：
  1. 不用正则，用 sqlglot 解析成 AST 再判断。正则挡不住 `/*x*/DROP`、`; DELETE`。
  2. 白名单而非黑名单：只允许 SELECT，且只允许访问语义层视图（不给 fact_* 明细表）。
  3. 强制 LIMIT + statement_timeout —— 防一条 SQL 打爆生产库。
  4. 执行账号是只读 role（chemq_ro）+ RLS。应用层校验 + 数据库权限 = 纵深防御，
     护栏被绕过时数据库仍然兜底。
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

from .config import settings
from .dialects import SENSITIVE_TOKENS_BY_DIALECT
from .domain import domain
from .schemas import GuardVerdict

# sqlglot 30 把 expressions.py 拆成了 expressions/ 包。包的 __init__.py 里，
# Select/Limit/Table 这些是 `from ... import *` 进来的（算再导出），而 Expression
# 是用显式命名 import 进来的、且没写冗余别名 `as Expression` —— 按 PEP 484 的
# 再导出规则，Pylance 就把它判成「私有导入」。所以 17 个 exp.X 里只有它报错。
# 类型本身没选错：.args 定义在 Expression 上，Expr（更上层的基类）没有这个属性。
SqlNode = exp.Expression  # pyright: ignore[reportPrivateImportUsage]

READ_ONLY_ROOTS = (exp.Select, exp.Union, exp.Except, exp.Intersect)

WRITE_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
    exp.Alter, exp.Merge, exp.Command,
)

# 敏感 token 表按方言放在 chemq/dialects.py（和月份写法、引擎名放在一起）。
# 兼容旧引用：默认仍是 Postgres 那一套
SENSITIVE_TOKENS = SENSITIVE_TOKENS_BY_DIALECT["postgres"]


def _qualified(table: exp.Table) -> str:
    db = table.text("db") or domain().schema          # 没写 schema 的表，按领域包的 schema 算
    return f"{db}.{table.name}".lower()


def check(
    sql: str,
    allowed_relations: tuple[str, ...],
    max_rows: int = 500,
    dialect: str | None = None,
) -> GuardVerdict:
    """返回 GuardVerdict；ok=True 时 rewritten_sql 是加过 LIMIT 的安全版本。

    dialect 留空则取 settings().sql_dialect —— 所以调用方不用关心引擎。
    sqlglot 的 AST 是方言无关的（exp.Select 就是 exp.Select），
    只有「解析」和「生成」两端需要知道方言，这正是当初不用正则的回报。
    """
    dialect = dialect or settings().sql_dialect
    violations: list[str] = []

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except Exception as e:  # noqa: BLE001
        return GuardVerdict(ok=False, violations=[f"SQL 解析失败: {e}"])

    if len(statements) != 1:
        return GuardVerdict(
            ok=False, violations=[f"只允许单条语句，实际解析出 {len(statements)} 条"]
        )

    stmt = statements[0]

    if not isinstance(stmt, READ_ONLY_ROOTS):
        return GuardVerdict(
            ok=False, violations=[f"只允许 SELECT，收到 {type(stmt).__name__.upper()}"]
        )

    for node_type in WRITE_NODES:
        if next(stmt.find_all(node_type), None) is not None:
            violations.append(f"包含写操作节点: {node_type.__name__.upper()}")

    allowed = {a.lower() for a in allowed_relations}
    cte_names = {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        if table.name.lower() in cte_names and not table.text("db"):
            continue
        if _qualified(table) not in allowed:
            violations.append(f"不允许访问 {_qualified(table)}；只能查语义层视图")

    # 一条取数 SQL 至少要引用一个真实的视图。不碰任何表的 SELECT
    # （SELECT 1、SELECT '查询被拒绝：…' AS 提示）语法合法、能执行、能返回一行，
    # 但它不是取数结果 —— 模型把"拒答"写成字面量时就长这样（Q010 真实发生过）。
    # 只引用 CTE、而 CTE 里也没有表的情况同理。
    real_tables = [
        t for t in stmt.find_all(exp.Table)
        if not (t.name.lower() in cte_names and not t.text("db"))
    ]
    if not real_tables:
        violations.append(
            "查询没有引用任何语义层视图，不是取数结果"
            "（常见于把拒答写成 SELECT '…' 字面量 —— 应改用 refuse_reason）"
        )

    lowered = sql.lower()
    for token in SENSITIVE_TOKENS_BY_DIALECT.get(dialect, SENSITIVE_TOKENS):
        if token in lowered:
            violations.append(f"包含敏感操作: {token.strip()}")

    if violations:
        return GuardVerdict(ok=False, violations=sorted(set(violations)))

    return GuardVerdict(ok=True, rewritten_sql=_force_limit(stmt, max_rows, dialect))


def _force_limit(stmt: SqlNode, max_rows: int, dialect: str = "postgres") -> str:
    """无 LIMIT 则加上；LIMIT 超上限则压到上限。"""
    current = stmt.args.get("limit")
    need_set = True
    if current is not None:
        try:
            need_set = int(current.expression.name) > max_rows
        except (AttributeError, ValueError):
            need_set = True
    if need_set:
        stmt.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    return stmt.sql(dialect=dialect)
