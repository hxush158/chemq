"""语义层目录（领域包的 semantic_catalog.md）—— text2sql 提示里给模型看的就是它。

先 make catalog（或 make seed）生成，再跑这些测试。
"""

import pytest

from chemq.domain import domain
from chemq.text2sql import schema_context, system_role

CATALOG = domain().catalog_path


@pytest.fixture(scope="module")
def text():
    if not CATALOG.exists():
        pytest.fail(f"{CATALOG} 不存在：先 make catalog（需要数据库已 make seed）")
    return CATALOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("leak", ["fact_", "received_cny", "revenue_cny", "CREATE ", " JOIN "])
def test_no_implementation_details_leak(text, leak):
    """W3 Q004：模型从视图定义里学到 fact_sales / received_cny，去查明细表。目录里不能再有这些。"""
    assert leak not in text


def test_lists_every_allowed_object(text):
    for name in ["v_plant_daily", "v_energy_daily", "v_sales_daily", "v_monthly_summary",
                 "dim_date", "dim_plant", "dim_product", "dim_customer", "dim_energy_type"]:
        assert f"### chem.{name}" in text


def test_lists_dimension_values(text):
    """W3 Q020：模型不知道装置全名，猜成了「合成氨」，查出 0 行。"""
    assert "P01 · 合成氨装置" in text
    assert "华南" in text and "甲醇" in text


def test_view_columns_are_semantic_names(text):
    assert "回款_cny" in text and "开车率_pct" in text


def test_catalog_carries_time_conventions(text):
    """W4：时间口径只写在 router 提示词里时，Agent 把"近三个月"算成了不含当前月。"""
    assert "## 时间口径约定" in text and "含基准月在内往前数 N 个自然月" in text


def test_schema_context_returns_catalog(text):
    assert schema_context("postgres") == text


def test_system_role_has_name_and_granularity_rules():
    r = system_role("postgres")
    assert "维度取值" in r and "一字不差" in r and "不要用 LIKE" in r
    assert "逐日" in r
    assert "fact_* 明细表一律不可访问" in r
