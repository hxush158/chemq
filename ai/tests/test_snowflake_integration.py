import os
from decimal import Decimal
from uuid import uuid4

import pytest
from snowflake.connector.errors import ProgrammingError

from chemq.config import settings
from chemq.db import SnowflakeEngine
from chemq.domain import domain

config = settings()

has_snowflake_config = all(
    value.strip()
    for value in (
        config.snowflake_account,
        config.snowflake_user,
        config.snowflake_private_key_path,
        config.snowflake_private_key_password,
    )
)

pytestmark = [
    pytest.mark.snowflake,
    pytest.mark.skipif(
        os.environ.get("CHEMQ_SNOWFLAKE_IT") != "1",
        reason="设置 CHEMQ_SNOWFLAKE_IT=1 才运行真库验收",
    ),
    pytest.mark.skipif(
        not has_snowflake_config,
        reason="缺少 Snowflake 连接配置",
    ),
]


@pytest.fixture(scope="module")
def sf_cursor():
    assert domain().id == "nz_power"
    assert settings().sql_dialect == "snowflake"
    assert settings().snowflake_database == "NZ_POWER"

    with SnowflakeEngine().readonly_cursor() as cur:
        cur.execute(
            """
            SELECT
                CURRENT_USER(),
                CURRENT_ROLE(),
                CURRENT_WAREHOUSE(),
                CURRENT_SCHEMA()
            """
        )
        assert cur.fetchone() == (
            "CHEMQ_REPORTER",
            "NZ_REPORTER",
            "WH_XS",
            "MART",
        )

        yield cur


def test_snapshot_matches(sf_cursor):
    current_domain = domain()
    snapshot = current_domain.config["eval"]["snapshot"]

    sf_cursor.execute(
        """
        SELECT
            COUNT(*),
            SUM(CAST(price_nzd_per_mwh AS DECIMAL(18, 2)))
        FROM mart_current_price
        WHERE trading_date <= %(as_of)s
        """,
        {"as_of": current_domain.as_of},
    )
    row_count, price_sum = sf_cursor.fetchone()

    assert row_count == snapshot["rows"]
    assert price_sum == Decimal(snapshot["price_sum"])


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM mart_current_price LIMIT 1",
        "SELECT * FROM mart_daily_price LIMIT 1",
        "SELECT * FROM v_dim_node LIMIT 1",
        "SELECT * FROM mart_daily_generation LIMIT 1",
        "SELECT * FROM mart_daily_generation_weighted_price LIMIT 1",
    ],
    ids=["current-price", "daily-price", "node", "generation", "weighted-price"],
)
def test_mart_relations_readable(sf_cursor, sql):
    sf_cursor.execute(sql)
    assert sf_cursor.fetchone() is not None


def test_allowlist_matches_the_relations_tested_here():
    """领域包允许查询的对象变了，上面"能读"的清单也要跟着变：多一个少一个都算失败。"""
    tested = {
        "mart_current_price", "mart_daily_price", "v_dim_node",
        "mart_daily_generation", "mart_daily_generation_weighted_price",
    }
    assert {name.split(".")[-1].lower() for name in domain().allowed_relations} == tested


def test_generation_snapshot_matches(sf_cursor):
    """发电两张表的核对值（nz-power 11/9 给的，DuckDB 服务库发布时核对的是同一组数）。"""
    current_domain = domain()
    snapshot = current_domain.config["eval"]["snapshot"]
    as_of = {"as_of": current_domain.as_of}

    sf_cursor.execute(
        "SELECT SUM(generation_kwh) FROM mart_daily_generation WHERE trading_date <= %(as_of)s",
        as_of,
    )
    (generation_sum,) = sf_cursor.fetchone()
    assert Decimal(str(generation_sum)) == Decimal(snapshot["generation_kwh_sum"])

    sf_cursor.execute(
        """
        SELECT COUNT(*), SUM(generation_kwh)
        FROM mart_daily_generation_weighted_price
        WHERE trading_date <= %(as_of)s
        """,
        as_of,
    )
    weighted_rows, weighted_sum = sf_cursor.fetchone()
    assert weighted_rows == snapshot["weighted_rows"]
    assert Decimal(str(weighted_sum)) == Decimal(snapshot["weighted_generation_kwh_sum"])


@pytest.mark.parametrize(
    "sql_template, expected_errors, cleanup_template",
    [
        # 2003 表示对象不存在或未授权；对象存在性须由建库流程确认。
        pytest.param(
            "SELECT * FROM NZ_POWER.CORE.dim_node LIMIT 1",
            {2003, 3001},
            None,
            id="deny-core",
        ),
        pytest.param(
            "SELECT * FROM NZ_POWER.RAW.PRICES_RAW LIMIT 1",
            {2003, 3001},
            None,
            id="deny-raw",
        ),
        pytest.param(
            "INSERT INTO mart_daily_price SELECT * FROM mart_daily_price LIMIT 0",
            {3001},
            None,
            id="deny-insert",
        ),
        pytest.param(
            "CREATE TABLE {name} (x INT)",
            {3001},
            "DROP TABLE {name}",
            id="deny-create-table",
        ),
        pytest.param(
            "CREATE VIEW {name} AS SELECT 1 AS x",
            {3001},
            "DROP VIEW {name}",
            id="deny-create-view",
        ),
    ],
)
def test_unauthorized_statements_rejected(
    sf_cursor, sql_template, expected_errors, cleanup_template
):
    name = f"chemq_probe_{uuid4().hex}"
    sql = sql_template.format(name=name)

    try:
        sf_cursor.execute(sql)
    except ProgrammingError as exc:
        assert exc.errno in expected_errors, (
            f"收到的不是预期权限错误，errno={exc.errno}，"
            f"sqlstate={exc.sqlstate}，错误={exc}"
        )
    else:
        # 只清理本次意外创建的对象，越权成功仍须判为失败。
        if cleanup_template is not None:
            sf_cursor.execute(cleanup_template.format(name=name))
        pytest.fail(f"越权语句竟然执行成功：{sql}")
