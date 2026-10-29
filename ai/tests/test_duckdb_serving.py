"""服务库构建测试；只使用 conftest.py 提供的临时源库。"""

import importlib.util
from datetime import date
from decimal import Decimal
from types import SimpleNamespace as NS

import duckdb
import pytest

from chemq.config import ROOT

AS_OF = date(2026, 9, 30)
OBJECTS = {
    "mart_current_price": "day",
    "mart_daily_price": "day",
    "v_dim_node": "first_seen_date",
}
SNAPSHOT = {"rows": 1, "price_sum": "12.50"}


@pytest.fixture(scope="module")
def builder():
    """加载真实脚本；不会执行其中的 if __name__ == '__main__'。"""
    path = ROOT / "scripts" / "build_duckdb_serving.py"
    spec = importlib.util.spec_from_file_location("serving_builder_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def built_db(builder, tiny_source_db, tmp_path):
    path = tmp_path / "built.duckdb"
    builder.build(tiny_source_db, path, OBJECTS, AS_OF)
    return path


@pytest.fixture
def publish_env(builder, tiny_source_db, tmp_path, monkeypatch):
    """只替换配置来源，main/build/verify/文件操作均使用真实实现。"""
    objects = dict(OBJECTS)
    snapshot = dict(SNAPSHOT)
    root = tmp_path / "domains" / "nz_power"
    d = NS(
        id="nz_power",
        schema="main",
        root=root,
        as_of=AS_OF.isoformat(),
        catalog={"views": list(OBJECTS), "dims": []},
        config={
            "serving": {"date_columns": objects},
            "eval": {"snapshot": snapshot},
        },
    )
    monkeypatch.setattr(builder, "domain", lambda: d)
    monkeypatch.setattr(
        builder, "settings", lambda: NS(duckdb_source=str(tiny_source_db))
    )
    return NS(
        source=tiny_source_db,
        target=root / "data" / "serving.duckdb",
        objects=objects,
        snapshot=snapshot,
    )


# 1. 精确比较对象集合：不能只断言 len(...) == 3。
def test_only_whitelisted_tables(built_db):
    with duckdb.connect(str(built_db), read_only=True) as con:
        actual = set(con.execute("""
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_catalog = current_database()
        """).fetchall())
    assert actual == {
        ("main", "mart_current_price", "BASE TABLE"),
        ("main", "mart_daily_price", "BASE TABLE"),
        ("main", "v_dim_node", "BASE TABLE"),
    }


# 2. 源库改名后仍可查，避免留下依赖源文件的视图。
def test_view_is_materialized(built_db, tiny_source_db):
    tiny_source_db.rename(tiny_source_db.with_name("source_unavailable.duckdb"))
    assert not tiny_source_db.exists()
    with duckdb.connect(str(built_db), read_only=True) as con:
        assert con.execute(
            "SELECT node_id FROM main.v_dim_node ORDER BY node_id"
        ).fetchall() == [(1,), (2,)]
        assert con.execute("""
            SELECT table_type FROM information_schema.tables
            WHERE table_catalog = current_database()
              AND table_schema = 'main' AND table_name = 'v_dim_node'
        """).fetchone() == ("BASE TABLE",)


# 3. 两张 mart 都要截断；截止日当天保留，之后的数据去掉。
@pytest.mark.parametrize("table", ["mart_current_price", "mart_daily_price"])
def test_as_of_cutoff_keeps_null_nodes(built_db, table):
    with duckdb.connect(str(built_db), read_only=True) as con:
        rows = con.execute(
            f'SELECT day, price_nzd_per_mwh FROM main."{table}" ORDER BY day'
        ).fetchall()
    assert rows == [(AS_OF, Decimal("12.50"))]


def test_v_dim_node_cutoff_keeps_null_and_drops_after_as_of(built_db):
    with duckdb.connect(str(built_db), read_only=True) as con:
        nodes = con.execute(
            "SELECT node_id, first_seen_date FROM main.v_dim_node ORDER BY node_id"
        ).fetchall()
    assert nodes == [(1, None), (2, date(2026, 6, 24))]


# 4. 快照不匹配：抛异常，首次生成不留下目标文件或临时文件。
@pytest.mark.parametrize("field, bad_value", [("rows", 2), ("price_sum", "99.00")])
def test_wrong_snapshot_leaves_no_files(builder, publish_env, field, bad_value):
    e = publish_env
    e.snapshot[field] = bad_value
    assert not e.target.exists()
    with pytest.raises(RuntimeError, match="快照不符"):
        builder.main()
    assert not e.target.exists()
    assert list(e.target.parent.iterdir()) == []


# 5. 重建失败：保留旧服务库，清除此次构建的临时文件。
def test_failed_rebuild_preserves_old_database(builder, publish_env):
    e = publish_env
    e.target.parent.mkdir(parents=True)
    builder.build(e.source, e.target, OBJECTS, AS_OF)
    # 旧库设不同的值，避免意外发布相同内容时无法识别。
    with duckdb.connect(str(e.target)) as con:
        con.execute("UPDATE mart_current_price SET price_nzd_per_mwh = 8.50")
    old_bytes = e.target.read_bytes()

    e.snapshot["rows"] = 2
    with pytest.raises(RuntimeError, match="快照不符"):
        builder.main()
    assert e.target.read_bytes() == old_bytes
    assert set(e.target.parent.iterdir()) == {e.target}
    with duckdb.connect(str(e.target), read_only=True) as con:
        assert con.execute(
            "SELECT price_nzd_per_mwh FROM mart_current_price"
        ).fetchall() == [(Decimal("8.50"),)]


# 6. 成功发布后，使用真实 DuckDBEngine 的连接配置查询服务库。
def test_published_database_works_with_engine(builder, publish_env, monkeypatch):
    from chemq import db

    e = publish_env
    source_before = e.source.read_bytes()
    builder.main()
    assert e.target.is_file()
    assert e.source.read_bytes() == source_before
    assert set(e.target.parent.iterdir()) == {e.target}

    monkeypatch.setattr(
        db,
        "settings",
        lambda: NS(
            duckdb_path=str(e.target),
            statement_timeout_ms=2000,
        ),
    )
    engine = db.DuckDBEngine()
    rows = engine.run_query("""
        SELECT day, price_nzd_per_mwh,
               current_setting('enable_external_access') AS external_access
        FROM main.mart_current_price
        ORDER BY day
    """)
    assert rows == [
        {
            "day": AS_OF,
            "price_nzd_per_mwh": Decimal("12.50"),
            "external_access": False,
        }
    ]
    assert (
        engine.run_query("""
        SELECT node_id, first_seen_date
        FROM main.v_dim_node ORDER BY node_id
    """)
        == [
            {"node_id": 1, "first_seen_date": None},
            {"node_id": 2, "first_seen_date": date(2026, 6, 24)},
        ]
    )
