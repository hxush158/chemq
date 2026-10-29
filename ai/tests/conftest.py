from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def tiny_source_db(tmp_path) -> Path:
    """假的源库：两张 mart、一张 fact、一个视图。"""
    path = tmp_path / "source.duckdb"
    con = duckdb.connect(str(path))

    try:
        con.execute("""
            CREATE TABLE mart_current_price (
                day DATE,
                price_nzd_per_mwh DECIMAL(10, 2)
            )
        """)

        # 第二行晚于测试约定的 as_of：2026-09-30。
        con.execute("""
            INSERT INTO mart_current_price VALUES
                ('2026-09-30', 12.50),
                ('2026-10-01', 99.00)
        """)

        con.execute("""
            CREATE TABLE mart_daily_price AS
            SELECT * FROM mart_current_price
        """)

        con.execute("""
            CREATE TABLE fact_private (
                node_id INTEGER,
                first_seen_date DATE
            )
        """)

        con.execute("""
            INSERT INTO fact_private VALUES
            (1, NULL),            -- 基线节点：从数据起点就存在 → 应保留
            (2, '2026-06-24'),    -- 数据期内出现的新节点 → 应保留
            (3, '2026-10-01')     -- as_of（2026-09-30）之后才出现 → 应删掉
        """)

        con.execute("""
            CREATE VIEW v_dim_node AS
            SELECT node_id, first_seen_date
            FROM fact_private
        """)

    finally:
        # 引擎稍后以只读模式重新连接，先关闭这里的写连接。
        con.close()

    return path


@pytest.fixture
def tiny_serving_db(tmp_path):
    path = tmp_path / "serving.duckdb"

    # 准备数据时使用普通可写连接。
    with duckdb.connect(str(path)) as con:
        con.execute("""
            CREATE TABLE mart_daily_price (
                day DATE,
                price_nzd_per_mwh DECIMAL(10, 2)
            )
        """)
        con.execute("""
            INSERT INTO mart_daily_price VALUES
                ('2026-09-29', 10.00),
                ('2026-09-30', 12.50)
        """)

    # 写连接已关闭，后续由引擎重新以只读模式打开。
    return path
