"""W4 的三个工具：不连数据库、不调 AWS。重点测"谁调都要过护栏"和"scope 模型改不了"。"""
import datetime as dt
import inspect
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from chemq import tools
from chemq.rag import Hit


def _no_db(*a, **k):
    raise AssertionError("护栏没拦住：不合规的 SQL 不该走到数据库")


@pytest.mark.parametrize("sql", [
    "DROP TABLE chem.fact_sales",
    "DELETE FROM chem.v_sales_daily",
    "SELECT * FROM chem.fact_sales",                       # 明细表不在白名单
    "SELECT 1; SELECT * FROM chem.v_plant_daily",           # 多语句
])
def test_guard_runs_before_db(sql):
    r = tools.run_semantic_query(sql, run=_no_db)
    assert r["ok"] is False and r["stage"] == "guard" and r["violations"]


def test_ok_query_gets_limit_scope_and_jsonable_rows():
    seen = {}

    def fake_run(sql, scope=None):
        seen.update(sql=sql, scope=scope)
        return [{"月份": dt.date(2026, 3, 1), "v": Decimal("134.75")}]

    r = tools.run_semantic_query("SELECT * FROM chem.v_monthly_summary", scope="华南", run=fake_run)
    assert r["ok"] and r["row_count"] == 1
    assert "LIMIT" in seen["sql"].upper()
    assert seen["scope"] == "华南"
    assert r["rows"] == [{"月份": "2026-03-01", "v": "134.75"}]     # Decimal / date 能直接 json


def test_db_error_is_returned_not_raised():
    def boom(sql, scope=None):
        raise RuntimeError('column "received_cny" does not exist')

    r = tools.run_semantic_query("SELECT 回款_cny FROM chem.v_sales_daily", run=boom)
    assert r["ok"] is False and r["stage"] == "execute" and "received_cny" in r["error"]


def test_rows_to_model_are_capped():
    r = tools.run_semantic_query("SELECT * FROM chem.v_plant_daily",
                                 run=lambda sql, scope=None: [{"v": i} for i in range(450)])
    assert r["row_count"] == 450 and r["truncated"] and len(r["rows"]) == tools.MAX_ROWS_TO_MODEL


def test_scope_is_not_a_parameter_the_model_can_fill():
    """MCP/Agent 暴露给模型的参数只有 sql；scope 是 keyword-only，由服务端绑定。"""
    p = inspect.signature(tools.run_semantic_query).parameters
    assert p["scope"].kind is inspect.Parameter.KEYWORD_ONLY


class FakeEmbedder:
    def embed(self, text):
        return NS(vector=[0.0] * 4)


def _hit(cos, content):
    return Hit(chunk_id=7, doc_id="KB-ENE-2026", title="t", content=content,
               rrf=0.02, cos_sim=cos, vec_rank=1, bm_rank=1)


def test_search_knowledge_no_hit_returns_nothing():
    r = tools.search_knowledge("帮我写一封辞职信", embedder=FakeEmbedder(),
                               search=lambda q, e: [_hit(0.1, "《办法》 › 第一条\n无关")])
    assert r == {"found": False, "hits": [], "note": "知识库里没有和这个问题相关的资料"}


def test_search_knowledge_hit_returns_source_and_text():
    r = tools.search_knowledge("检修期间能耗怎么考核", embedder=FakeEmbedder(),
                               search=lambda q, e: [_hit(0.6, "《办法》 › 第三章 › 第八条\n检修月单耗不考核。")])
    assert r["found"] and r["hits"][0]["source"] == "《办法》 › 第三章 › 第八条"
    assert r["hits"][0]["ref"] == "KB-ENE-2026#7"
    assert r["hits"][0]["text"] == "检修月单耗不考核。"


def test_list_metrics_has_dictionary_and_catalog():
    text = tools.list_metrics()
    assert "指标口径字典" in text and "语义层目录" in text and "合成氨装置" in text


def test_mcp_server_builds():
    pytest.importorskip("mcp")
    from chemq.mcp_server import build_server
    assert build_server(scope="华南") is not None
