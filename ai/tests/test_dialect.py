"""多引擎接缝的单测 —— 不需要任何 Doris 实例就能跑。

这个文件存在的意义：W5 才接真实 Doris，但「换引擎时哪些东西会碎」现在就能测。
sqlglot 的 AST 是方言无关的（exp.Select 就是 exp.Select），
只有解析和生成两端要知道方言 —— 所以护栏的**结构性**保证换引擎后依然成立，
这一点可以在没有数据库的情况下证明。这正是当初不用正则的回报。
"""
import pytest

from chemq.config import settings
from chemq.db import _ENGINES, DorisEngine, DuckDBEngine, PostgresEngine, SnowflakeEngine
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
    duckdb = SENSITIVE_TOKENS_BY_DIALECT["duckdb"]

    # 不同引擎的敏感函数和语句写法不同，分别维护拦截规则。
    assert "pg_read_file" in pg and "pg_read_file" not in doris
    assert "into outfile" in doris and "into outfile" not in pg
    assert "read_csv" in duckdb and "read_csv" not in pg and "read_csv" not in doris

    # 两边都得拦的
    for common in ("information_schema", "set role"):
        assert common in pg and common in doris and common in duckdb


@pytest.mark.parametrize("sql", [
    "SELECT * FROM chem.v_sales_daily INTO OUTFILE 's3://bucket/dump'",
    "SELECT * FROM s3('s3://bucket/x.parquet', 'format' = 'parquet')",
    "SELECT sleep(30)",
])
def test_doris_specific_exfiltration_blocked(sql):
    """Doris 特有的越权通道。注意：INTO OUTFILE 可能连解析都过不了 ——
    那也算拦住了，所以这里只断言 not ok，不断言拦截理由。"""
    assert not check(sql, ALLOWED, max_rows=500, dialect="doris").ok, sql


# NZ 的白名单。名字不要叫 ALLOWED：文件开头已经有一个 ALLOWED（化工的），
# 重名会覆盖它，前面的测试会跟着出错（上一轮就是这个问题）。
NZ_ALLOWED = ("main.mart_current_price", "main.mart_daily_price", "main.v_dim_node")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_parquet('s3://bucket/x.parquet')",
    "SELECT * FROM read_text('/etc/passwd')",
    "SELECT * FROM glob('/*')",
    "SELECT * FROM '/etc/passwd'",                                       # 直接把字符串当文件查
    "SELECT getenv('HOME')",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM main.mart_daily_price, read_csv('/etc/passwd')",     # 夹在正常查询里
])
def test_duckdb_specific_exfiltration_blocked(sql):
    assert not check(sql, NZ_ALLOWED, max_rows=500, dialect="duckdb").ok, sql


def test_duckdb_plain_select_passes():
    v = check("SELECT trading_date, avg_price FROM main.mart_daily_price",
              NZ_ALLOWED, max_rows=500, dialect="duckdb")
    assert v.ok and "LIMIT" in v.rewritten_sql.upper()


# ---------- ④ prompt 按方言渲染 ----------

def test_system_role_renders_per_dialect():
    pg = system_role("postgres")
    doris = system_role("doris")
    duckdb = system_role("duckdb")

    assert "PostgreSQL 16" in pg and "to_char" in pg
    assert "Apache Doris" in doris and "date_format" in doris
    assert "DuckDB" in duckdb and "strftime" in duckdb

    # 最容易犯的错是维护两份 prompt 然后漂移。模板只有一份，所以
    # 除了方言相关的那几处，其余内容必须完全一致。
    assert pg.count("\n") == doris.count("\n")
    assert pg.count("\n") == duckdb.count("\n")
    for rule in ("只能 SELECT", "指标口径必须引用指标字典", "不要把整张排名表甩给用户"):
        assert rule in pg and rule in doris and rule in duckdb


def test_unknown_dialect_fails_loudly():
    with pytest.raises(ValueError):
        system_role("mysql")
    assert set(DIALECT_PROFILE) == {
        "postgres", "doris", "duckdb", "snowflake",
    }
    assert set(SENSITIVE_TOKENS_BY_DIALECT) == set(DIALECT_PROFILE)


# ---------- ⑤ 未实现的引擎必须响亮地失败 ----------

def test_doris_engine_raises_instead_of_returning_empty():
    """最危险的失败模式是「静默返回空结果」—— 用户会以为查出来是 0。
    W5 之前 DorisEngine 必须抛错。"""
    e = DorisEngine()
    assert e.dialect == "doris"
    with pytest.raises(NotImplementedError):
        e.run_query("SELECT 1")
    with pytest.raises(NotImplementedError):
        e.readonly_cursor(scope="华南")


def test_sqlguard_unknown_dialect_fails_loudly():
    with pytest.raises(ValueError, match="mysql"):
        check("SELECT 1 FROM chem.v_plant_daily", ALLOWED, dialect="mysql")


def test_engine_registry():
    assert _ENGINES["postgres"] is PostgresEngine
    assert _ENGINES["doris"] is DorisEngine
    assert _ENGINES["duckdb"] is DuckDBEngine
    assert _ENGINES["snowflake"] is SnowflakeEngine

# ---------- ⑥  Snowflake 特定测试 ----------

SF_ALLOWED = ("mart.mart_daily_price",)

SF_STATEMENTS = [
    "PUT file:///tmp/x.csv @mystage",
    "GET @mystage file:///tmp/",
    "COPY INTO mart.mart_daily_price FROM @mystage",
    "COPY INTO @mystage FROM (SELECT * FROM mart.mart_daily_price)",
    "LIST @mystage",
    "REMOVE @mystage",
    "CALL my_proc()",
    "EXECUTE IMMEDIATE 'SELECT 1'",
]


@pytest.mark.parametrize("sql", SF_STATEMENTS)
def test_snowflake_statement_roots_blocked(monkeypatch, sql):
    # 排除 token 干扰，证明根节点白名单本身就能拒绝。
    monkeypatch.setitem(SENSITIVE_TOKENS_BY_DIALECT, "snowflake", ())

    v = check(sql, SF_ALLOWED, dialect="snowflake")

    assert not v.ok
    assert any(x.startswith("只允许 SELECT") for x in v.violations)


@pytest.mark.parametrize("sql", SF_STATEMENTS)
@pytest.mark.parametrize("position", ["before", "after"])
def test_snowflake_multiple_statements_blocked(monkeypatch, sql, position):
    monkeypatch.setitem(SENSITIVE_TOKENS_BY_DIALECT, "snowflake", ())
    normal = "SELECT * FROM mart.mart_daily_price"
    combined = (
        f"{sql}; {normal}" if position == "before"
        else f"{normal}; {sql}"
    )

    v = check(combined, SF_ALLOWED, dialect="snowflake")

    assert not v.ok
    assert any(x.startswith("只允许单条语句") for x in v.violations)


@pytest.mark.parametrize("expression", [
    "IDENTIFIER('avg_price')",
    "IDENTIFIER ('avg_price')",
    "IDENTIFIER/*c*/('avg_price')",
])
def test_snowflake_dynamic_identifier_blocked(monkeypatch, expression):
    # 排除 "identifier(" token，单独验证 AST 检查。
    monkeypatch.setitem(SENSITIVE_TOKENS_BY_DIALECT, "snowflake", ())

    v = check(
        f"SELECT {expression} FROM mart.mart_daily_price",
        SF_ALLOWED,
        dialect="snowflake",
    )

    assert not v.ok
    assert "包含敏感操作: 动态标识符 IDENTIFIER" in v.violations


@pytest.mark.parametrize("token, sql", [
    ("@", "SELECT * FROM @mystage"),
    (
        "identifier(",
        "SELECT IDENTIFIER('avg_price') FROM mart.mart_daily_price",
    ),
    (
        "result_scan",
        "SELECT * FROM mart.mart_daily_price, "
        "TABLE(RESULT_SCAN(LAST_QUERY_ID()))",
    ),
    (
        "infer_schema",
        "SELECT * FROM mart.mart_daily_price, "
        "TABLE(INFER_SCHEMA(LOCATION => '@mystage', FILE_FORMAT => 'fmt'))",
    ),
    (
        "system$",
        "SELECT SYSTEM$GET_SNOWFLAKE_PLATFORM_INFO() "
        "FROM mart.mart_daily_price",
    ),
    (
        "snowflake.account_usage",
        "SELECT * FROM snowflake.account_usage.query_history",
    ),
    (
        "get_presigned_url",
        "SELECT GET_PRESIGNED_URL(@mystage, 'x.csv') "
        "FROM mart.mart_daily_price",
    ),
    (
        "build_scoped_file_url",
        "SELECT BUILD_SCOPED_FILE_URL(@mystage, 'x.csv') "
        "FROM mart.mart_daily_price",
    ),
])
def test_snowflake_sensitive_tokens_blocked(token, sql):
    v = check(sql, SF_ALLOWED, dialect="snowflake")

    assert not v.ok
    # 即使还触发了其他规则，也必须证明对应 token 被命中。
    assert f"包含敏感操作: {token}" in v.violations


@pytest.mark.parametrize(
    "dialect", ["postgres", "doris", "duckdb", "snowflake"]
)
@pytest.mark.parametrize("catalog", ["OTHER_DB", "NZ_POWER"])
def test_catalog_qualified_table_is_blocked(dialect, catalog):
    # schema.table 本身在白名单中，证明拒绝原因确实是显式库名。
    v = check(
        f"SELECT * FROM {catalog}.chem.v_plant_daily",
        ALLOWED,
        dialect=dialect,
    )

    assert not v.ok
    assert "不允许使用带库名的三段式表名" in v.violations


@pytest.mark.parametrize("sql", [
    "SELECT avg_price AS target FROM mart.mart_daily_price",
    "WITH x AS (SELECT * FROM mart.mart_daily_price) SELECT * FROM x",
    "SELECT * FROM mart.mart_daily_price "
    "UNION SELECT * FROM mart.mart_daily_price",
])
def test_snowflake_normal_queries_still_pass(sql):
    v = check(sql, SF_ALLOWED, dialect="snowflake")

    assert v.ok, v.violations
    assert v.rewritten_sql is not None
    assert "LIMIT 500" in v.rewritten_sql.upper()


# ---------- ⑦ 验证默认 schema 和实际白名单都随方言切换 ----------
@pytest.mark.parametrize("dialect, expected_schema", [
    ("snowflake", "mart"),
    ("duckdb", "main"),
])
def test_nz_schema_is_per_dialect(monkeypatch, dialect, expected_schema):
    from chemq.domain import load_domain

    monkeypatch.setattr(settings(), "sql_dialect", dialect)
    d = load_domain("nz_power")

    assert d.schema == expected_schema
    assert d.allowed_relations == (
        f"{expected_schema}.mart_current_price",
        f"{expected_schema}.mart_daily_price",
        f"{expected_schema}.v_dim_node",
    )

# ---------- ⑧ chem 回归测试，检查它在支持的两个方言下，白名单和正常查询结果保持一致 ----------
def test_chem_relations_do_not_change(monkeypatch):
    from chemq.domain import load_domain

    d = load_domain("chem")

    monkeypatch.setattr(settings(), "sql_dialect", "postgres")
    assert d.schema == "chem"
    original = d.allowed_relations
    assert original and all(x.startswith("chem.") for x in original)

    for dialect in ("postgres", "doris"):
        monkeypatch.setattr(settings(), "sql_dialect", dialect)

        assert d.schema == "chem"
        assert d.allowed_relations == original

        v = check(
            "SELECT * FROM chem.v_plant_daily",
            d.allowed_relations,
            dialect=dialect,
        )
        assert v.ok, v.violations