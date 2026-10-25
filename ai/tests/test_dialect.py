"""多引擎接缝的单测 —— 不需要任何 Doris 实例就能跑。

这个文件存在的意义：W5 才接真实 Doris，但「换引擎时哪些东西会碎」现在就能测。
sqlglot 的 AST 是方言无关的（exp.Select 就是 exp.Select），
只有解析和生成两端要知道方言 —— 所以护栏的**结构性**保证换引擎后依然成立，
这一点可以在没有数据库的情况下证明。这正是当初不用正则的回报。
"""
import pytest

from chemq.config import settings
from chemq.db import _ENGINES, DorisEngine, PostgresEngine
from chemq.sqlguard import SENSITIVE_TOKENS_BY_DIALECT, check
from chemq.text2sql import DIALECT_PROFILE, system_role

ALLOWED = ("chem.v_plant_daily", "chem.v_sales_daily", "chem.dim_plant")


# ---------- ① 默认行为没被改动 ----------

def test_default_dialect_is_postgres():
    """加了方言开关之后，什么都不配时必须还是 postgres —— W1-W4 的评测靠这条。"""
    assert settings().sql_dialect == "postgres"


# ---------- ② 护栏的结构性保证是方言无关的 ----------

@pytest.mark.parametrize("dialect", ["postgres", "doris"])
def test_plain_select_passes_and_gets_limit_in_both_dialects(dialect):
    v = check(
        "SELECT date_key, 产量_t FROM chem.v_plant_daily",
        ALLOWED, max_rows=500, dialect=dialect,
    )
    assert v.ok, v.violations
    assert v.rewritten_sql is not None
    assert "LIMIT 500" in v.rewritten_sql.upper()


@pytest.mark.parametrize("dialect", ["postgres", "doris"])
@pytest.mark.parametrize("sql", [
    "DELETE FROM chem.fact_sales",
    "UPDATE chem.fact_sales SET revenue_cny = 0",
    "SELECT 1; DROP TABLE chem.fact_sales",
    "SELECT * FROM chem.fact_sales",                       # 明细表不在白名单
    "SELECT * FROM chem.v_sales_daily UNION SELECT * FROM chem.fact_sales",
])
def test_structural_attacks_blocked_in_both_dialects(dialect, sql):
    """写操作、多语句、越表 —— 这些靠 AST 拦，和方言无关。"""
    assert not check(sql, ALLOWED, max_rows=500, dialect=dialect).ok, sql


def test_whitelist_needs_no_change_across_dialects():
    """Doris 没有 schema，只有 database。但只要 Doris 侧的 database 也叫 chem，
    领域包的白名单（domain().allowed_relations）一个字都不用改 —— 这条就是在钉住这个设计决定。"""
    sql = "SELECT 产量_t FROM chem.v_plant_daily"
    assert check(sql, ALLOWED, dialect="postgres").ok
    assert check(sql, ALLOWED, dialect="doris").ok


# ---------- ③ 敏感 token 是按引擎走的 ----------

def test_sensitive_tokens_are_per_dialect():
    pg = SENSITIVE_TOKENS_BY_DIALECT["postgres"]
    doris = SENSITIVE_TOKENS_BY_DIALECT["doris"]

    # Postgres 的拖库通道在 Doris 上不存在，反之亦然
    assert "pg_read_file" in pg and "pg_read_file" not in doris
    assert "into outfile" in doris and "into outfile" not in pg

    # 两边都得拦的
    for common in ("information_schema", "set role"):
        assert common in pg and common in doris


@pytest.mark.parametrize("sql", [
    "SELECT * FROM chem.v_sales_daily INTO OUTFILE 's3://bucket/dump'",
    "SELECT * FROM s3('s3://bucket/x.parquet', 'format' = 'parquet')",
    "SELECT sleep(30)",
])
def test_doris_specific_exfiltration_blocked(sql):
    """Doris 特有的越权通道。注意：INTO OUTFILE 可能连解析都过不了 ——
    那也算拦住了，所以这里只断言 not ok，不断言拦截理由。"""
    assert not check(sql, ALLOWED, max_rows=500, dialect="doris").ok, sql


# ---------- ④ prompt 按方言渲染 ----------

def test_system_role_renders_per_dialect():
    pg = system_role("postgres")
    doris = system_role("doris")

    assert "PostgreSQL 16" in pg and "to_char" in pg
    assert "Apache Doris" in doris and "date_format" in doris

    # 最容易犯的错是维护两份 prompt 然后漂移。模板只有一份，所以
    # 除了方言相关的那几处，其余内容必须完全一致。
    assert pg.count("\n") == doris.count("\n")
    for rule in ("只能 SELECT", "指标口径必须引用指标字典", "不要把整张排名表甩给用户"):
        assert rule in pg and rule in doris


def test_unknown_dialect_fails_loudly():
    with pytest.raises(ValueError):
        system_role("mysql")
    assert set(DIALECT_PROFILE) == {"postgres", "doris"}


# ---------- ⑤ 未实现的引擎必须响亮地失败 ----------

def test_doris_engine_raises_instead_of_returning_empty():
    """最危险的失败模式是「静默返回空结果」—— 用户会以为查出来是 0。
    W5 之前 DorisEngine 必须抛错。"""
    e = DorisEngine()
    assert e.dialect == "doris"
    with pytest.raises(NotImplementedError):
        e.run_query("SELECT 1")
    with pytest.raises(NotImplementedError):
        e.readonly_cursor(region="华南")


def test_engine_registry():
    assert _ENGINES["postgres"] is PostgresEngine
    assert _ENGINES["doris"] is DorisEngine
