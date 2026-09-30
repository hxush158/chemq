import pytest
from chemq.sqlguard import check

ALLOWED = ("chem.v_plant_daily", "chem.v_sales_daily", "chem.dim_plant")


def g(sql):
    return check(sql, ALLOWED, max_rows=500)


def test_plain_select_passes_and_gets_limit():
    v = g("SELECT date_key, 产量_t FROM chem.v_plant_daily WHERE plant_id = 'P01'")
    assert v.ok
    assert v.rewritten_sql is not None
    assert "LIMIT 500" in v.rewritten_sql.upper()


def test_cte_is_allowed():
    v = g("WITH x AS (SELECT * FROM chem.v_plant_daily) SELECT count(*) FROM x")
    assert v.ok, v.violations


def test_union_unauthorized_table_is_blocked():
    """测试通过 UNION 拼接偷查非白名单表（fact_sales）会被拦截"""
    sql = "SELECT * FROM chem.v_sales_daily UNION SELECT * FROM chem.fact_sales"
    v = g(sql)
    assert not v.ok, "应该拦截：UNION 中引用了非白名单表 chem.fact_sales"


def test_cte_hiding_delete_is_blocked():
    """测试在 CTE 子句中隐藏 DELETE 写操作会被拦截"""
    sql = "WITH x AS (DELETE FROM chem.fact_sales RETURNING *) SELECT * FROM x"
    v = g(sql)
    assert not v.ok, "应该拦截：WITH 子句内隐藏了 DELETE 写操作"


def test_subquery_unauthorized_table_is_blocked():
    """测试在 WHERE 子查询深层引用非白名单表会被拦截"""
    sql = "SELECT * FROM chem.v_plant_daily WHERE plant_id IN (SELECT plant_id FROM chem.fact_sales)"
    v = g(sql)
    assert not v.ok, "应该拦截：子查询中嵌套引用了非白名单表 chem.fact_sales"

@pytest.mark.parametrize("sql", [
    "DELETE FROM chem.fact_sales",
    "SELECT 1; DROP TABLE chem.fact_sales",
    "UPDATE chem.fact_sales SET revenue_cny = 0",
    "SELECT * FROM chem.fact_sales",                      # 明细表不在白名单
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT pg_sleep(30)",
    "INSERT INTO chem.dim_plant VALUES ('x','x','x',1,'2020-01-01')",
])
def test_blocked(sql):
    v = g(sql)
    assert not v.ok, sql


def test_oversized_limit_is_capped():
    v = g("SELECT * FROM chem.v_sales_daily LIMIT 100000")
    assert v.ok
    assert v.rewritten_sql is not None
    assert "LIMIT 500" in v.rewritten_sql.upper()
