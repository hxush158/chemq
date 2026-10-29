import time
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import duckdb
import pytest

from chemq import db
from chemq.config import settings
from chemq.db import DuckDBEngine


def test_mart_readable_with_external_access_disabled(
    duckdb_engine,
):
    # 同一条受限连接中，确认配置关闭且主库可读。
    with duckdb_engine.readonly_cursor() as con:
        enabled = con.execute(
            "SELECT current_setting('enable_external_access')"
        ).fetchone()[0]
        assert enabled is False

        count = con.execute("SELECT count(*) FROM main.mart_daily_price").fetchone()[0]
        assert count == 2

    # 再验证公开查询接口返回正确的业务值。
    rows = duckdb_engine.run_query("""
        SELECT day, price_nzd_per_mwh
        FROM main.mart_daily_price
        WHERE day = DATE '2026-09-30'
    """)

    assert rows == [
        {
            "day": date(2026, 9, 30),
            "price_nzd_per_mwh": Decimal("12.50"),
        }
    ]


@pytest.fixture
def duckdb_engine(tiny_serving_db, monkeypatch):
    config = SimpleNamespace(
        duckdb_path=str(tiny_serving_db),
        statement_timeout_ms=2000,
    )
    monkeypatch.setattr(db, "settings", lambda: config)

    return db.DuckDBEngine()


@pytest.fixture
def engine_config(tiny_source_db, monkeypatch):
    config = SimpleNamespace(
        duckdb_path=str(tiny_source_db),
        statement_timeout_ms=2000,
    )
    monkeypatch.setattr(db, "settings", lambda: config)


@pytest.mark.parametrize("use_system_path", [True, False])
def test_read_csv_is_blocked(duckdb_engine, tmp_path, use_system_path):
    if use_system_path:
        path = "/etc/passwd"
    else:
        csv = tmp_path / "outside.csv"
        csv.write_text("value\n7\n", encoding="utf-8")
        path = str(csv)

        # 正向对照：确认文件存在、格式正确，普通连接能读。
        with duckdb.connect() as con:
            assert con.execute(
                "SELECT value FROM read_csv($path)",
                {"path": path},
            ).fetchall() == [(7,)]

    with pytest.raises(
        duckdb.PermissionException,
        match="(?i)disabled|external",
    ):
        duckdb_engine.run_query(
            "SELECT * FROM read_csv($path)",
            params={"path": path},
        )


@pytest.mark.parametrize(
    "sql_template, expected_error, message",
    [
        pytest.param(
            "SELECT * FROM read_csv('/etc/passwd')",
            duckdb.PermissionException,
            "(?i)disabled|external",
            id="read_csv",
        ),
        pytest.param(
            "SELECT * FROM '/etc/passwd'",
            (
                duckdb.PermissionException,
                duckdb.CatalogException,
            ),
            "(?i)disabled|external|" "Table with name /etc/passwd does not exist",
            id="implicit_passwd",
        ),
        pytest.param(
            "SELECT * FROM read_text('/etc/passwd')",
            duckdb.PermissionException,
            "(?i)disabled|external",
            id="read_text",
        ),
        pytest.param(
            "SELECT * FROM glob('/*')",
            duckdb.PermissionException,
            "(?i)disabled|external",
            id="glob",
        ),
        pytest.param(
            "COPY (SELECT 1) TO {output}",
            duckdb.PermissionException,
            "(?i)disabled|external",
            id="copy_to",
        ),
    ],
)
def test_engine_blocks_external_access(
    engine_config,
    tmp_path,
    sql_template,
    expected_error,
    message,
):
    output_path = tmp_path / "x.csv"

    # 先确认目标文件原本不存在。
    assert not output_path.exists()

    # 把路径转换成 SQL 字符串字面量，转义单引号。
    output_literal = "'" + str(output_path).replace("'", "''") + "'"
    sql = sql_template.format(output=output_literal)

    # 直接调用真实引擎，完全不经过 sqlguard。
    with pytest.raises(expected_error, match=message):
        db.DuckDBEngine().run_query(sql)

    # COPY 即使报错，也不能留下已创建的文件。
    assert not output_path.exists()


@pytest.mark.parametrize(
    "sql",
    [
        "SET enable_external_access = true",
        "SET threads = 4",
    ],
)
def test_configuration_is_locked(duckdb_engine, sql):
    with pytest.raises(duckdb.Error, match="(?i)locked"):
        duckdb_engine.run_query(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t(a INT)",
        """
    INSERT INTO main.mart_daily_price
        (day, price_nzd_per_mwh)
    VALUES (DATE '2026-09-30', 1.00)
    """,
    ],
)
def test_database_is_readonly(duckdb_engine, sql):
    before = duckdb_engine.run_query("SELECT count(*) AS n FROM main.mart_daily_price")

    with pytest.raises(duckdb.Error, match="(?i)read.only"):
        duckdb_engine.run_query(sql)

    # 插入没有生效。
    after = duckdb_engine.run_query("SELECT count(*) AS n FROM main.mart_daily_price")
    assert after == before

    # 建表没有生效。
    tables = duckdb_engine.run_query("""
        SELECT count(*) AS n
        FROM information_schema.tables
        WHERE table_catalog = current_database()
          AND table_schema = 'main'
          AND table_name = 't'
    """)
    assert tables == [{"n": 0}]


@pytest.mark.parametrize("mode", ["", " (READ_ONLY)"])
def test_attach_is_blocked(duckdb_engine, tmp_path, mode):
    other_path = tmp_path / "other.duckdb"

    # 创建真实有效的数据库，排除“文件不存在”的干扰。
    with duckdb.connect(str(other_path)) as con:
        con.execute("CREATE TABLE probe(a INT)")

    assert other_path.is_file()

    path_literal = "'" + str(other_path).replace("'", "''") + "'"
    sql = f"ATTACH {path_literal} AS o{mode}"

    # 正向对照：普通连接能成功挂载。
    with duckdb.connect() as con:
        con.execute(sql)
        assert con.execute("SELECT count(*) FROM o.main.probe").fetchone() == (0,)

    # 受限引擎必须拒绝同一条 SQL。
    with pytest.raises(
        duckdb.PermissionException,
        match="(?i)disabled|external",
    ):
        duckdb_engine.run_query(sql)


def test_slow_query_is_interrupted(tiny_serving_db, monkeypatch):
    config = settings()

    monkeypatch.setattr(config, "duckdb_path", str(tiny_serving_db))
    monkeypatch.setattr(config, "statement_timeout_ms", 300)

    engine = DuckDBEngine()

    started = time.perf_counter()

    with pytest.raises(duckdb.InterruptException):
        engine.run_query("""
            SELECT sum(a.range * b.range)
            FROM range(100000) a, range(100000) b
        """)

    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, f"中断过晚，耗时 {elapsed:.3f} 秒"


@pytest.mark.parametrize(
    "session_var",
    [None, "test.scope"],
    ids=["domain_without_scope", "domain_with_scope"],
)
def test_duckdb_scope_fails_before_connect(duckdb_engine, monkeypatch, session_var):
    monkeypatch.setattr(
        db,
        "domain",
        lambda: NS(
            id="test_domain",
            scope_session_var=session_var,
        ),
    )

    # 一旦尝试连接，立即让测试失败。
    connect = Mock(
        side_effect=AssertionError("应该先拒绝 scope，不应该调用 duckdb.connect")
    )
    monkeypatch.setattr(duckdb, "connect", connect)

    with pytest.raises(ValueError, match="scope"):
        duckdb_engine.run_query(
            "SELECT * FROM main.mart_daily_price",
            scope="华南",
        )

    connect.assert_not_called()
