"""W3 检索层的单测 —— 不连数据库、不调 Bedrock。

W3 的主题是"每一环都要能单独测"。这里测的是切词、SQL 形状和无命中判断；
召回效果（Recall@5）要等 ingest 之后在真实数据上测。
"""
import pytest

from chemq import rag
from chemq.rag import (
    HYBRID_SQL,
    Hit,
    hit_signals,
    hybrid_search,
    is_no_hit,
    load_glossary,
    vector_literal,
)
from chemq.textseg import NO_LEXEME, doc_lexemes, identifiers, lexemes, to_tsquery_literal

DOC = "单位产品综合能耗按 GB/T 2589-2020 规定的折标系数折算，分母为合格产量。"


# ================= ① 中文切词 =================

def test_chinese_is_split_into_bigrams():
    """'simple' 会把整句当一个词项；bigram 之后「能耗」能单独匹配。"""
    assert {"综合", "能耗", "折标", "合格"} <= set(doc_lexemes(DOC))


@pytest.mark.parametrize("query, must_share", [
    ("能耗怎么算", {"能耗"}),                      # 意图模糊的问法：靠中文二元组
    ("GB/T 2589", {"gb/t", "2589"}),               # 标识符：整体和拆分都能对上
    ("折标煤系数依据哪个标准", {"折标", "系数"}),
])
def test_query_and_doc_share_lexemes(query, must_share):
    assert must_share <= (set(lexemes(query)) & set(doc_lexemes(DOC)))


def test_identifier_parts_are_indexed():
    """文档写的是 GB/T 2589-2020，用户只问 2589 也要能命中。"""
    assert {"2589-2020", "2589", "2020", "gb/t"} <= set(doc_lexemes(DOC))


def test_unrelated_query_shares_nothing():
    assert not (set(lexemes("帮我写一封辞职信")) & set(doc_lexemes(DOC)))


def test_tsquery_literal_is_or_of_quoted_lexemes():
    q = to_tsquery_literal("GB/T 2589")
    assert q == "'2589' | 'gb' | 'gb/t'"
    assert "&" not in q                     # 不能用 AND：问句里的虚词二元组文档里没有


def test_empty_query_matches_nothing():
    assert to_tsquery_literal("？！。") == NO_LEXEME


def test_identifiers():
    assert identifiers("折标系数依据 GB/T 2589，装置 P02") == ["2589", "gb/t", "p02"]
    assert identifiers("综合能耗怎么算") == []


# ================= ② SQL 形状 =================

def test_each_leg_orders_and_limits_before_fusing():
    """原来的写法 `row_number() OVER (...) ... LIMIT 20` 不保证取到前 20 名，也用不上 HNSW。"""
    vec_leg = HYBRID_SQL.split("bm AS")[0]
    assert "ORDER BY embedding <=> %(emb)s::vector" in vec_leg
    assert vec_leg.index("ORDER BY embedding") < vec_leg.index("LIMIT %(n_vec)s")


def test_bm25_uses_literal_tsquery_not_parser():
    """plainto_tsquery / to_tsquery 会再用 'simple' 解析器切一遍，又切不了中文。"""
    assert "%(tsq)s::tsquery" in HYBRID_SQL
    assert "plainto_tsquery" not in HYBRID_SQL
    assert "to_tsvector('simple'" not in HYBRID_SQL


def test_vector_leg_exposes_raw_similarity():
    assert "cos_sim" in HYBRID_SQL


def test_vector_literal():
    assert vector_literal([0.1, -0.25, 1.0]) == "[0.1,-0.25,1]"


def test_hybrid_search_passes_params(monkeypatch):
    seen = {}

    def fake_run_query(sql, region=None, params=None):
        seen.update(params or {})
        return [dict(chunk_id=1, doc_id="D1", title="能耗考核办法", content=DOC,
                     rrf=0.03, cos_sim=0.61, vec_rank=1, bm_rank=2)]

    monkeypatch.setattr(rag, "run_query", fake_run_query)
    hits = hybrid_search("GB/T 2589", [0.0] * 1024, top_k=3)
    assert seen["tsq"] == "'2589' | 'gb' | 'gb/t'"
    assert seen["emb"].startswith("[0,0,")
    assert seen["top_k"] == 3
    assert seen["rrf_k"] == 60 and seen["n_vec"] == 20 and seen["n_bm"] == 20
    assert isinstance(hits[0], Hit) and hits[0].to_citation().doc_id == "D1"


@pytest.mark.parametrize("mode, n_vec, n_bm", [
    ("hybrid", 20, 20), ("vector", 20, 0), ("bm25", 0, 20),
])
def test_mode_switches_off_one_leg(monkeypatch, mode, n_vec, n_bm):
    """评测用：关掉一路 = 那一路 LIMIT 0，三种方式跑同一段 SQL。"""
    seen = {}
    monkeypatch.setattr(rag, "run_query", lambda sql, region=None, params=None: seen.update(params or {}) or [])
    hybrid_search("能耗怎么算", [0.0] * 1024, mode=mode)
    assert (seen["n_vec"], seen["n_bm"]) == (n_vec, n_bm)


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="mode"):
        hybrid_search("x", [0.0] * 1024, mode="dense")


# ================= ③ 无命中判断 =================

def _hit(cos, content="能耗考核办法正文", bm=None):
    return Hit(chunk_id=1, doc_id="D1", title="t", content=content,
               rrf=1 / 61, cos_sim=cos, vec_rank=1, bm_rank=bm)


def test_rrf_rank_alone_is_not_relevance():
    """第一名永远是 1/61，哪怕问题和知识库毫无关系。"""
    assert is_no_hit([_hit(0.18)], "帮我写一封辞职信", min_cosine=0.35)


def test_high_cosine_is_a_hit():
    assert not is_no_hit([_hit(0.62)], "能耗怎么算", min_cosine=0.35)


def test_identifier_found_rescues_low_cosine():
    """向量最弱、BM25 最强的场景：余弦低，但标识符在正文里字面出现。"""
    hits = [_hit(0.21, "折标系数按 GB/T 2589-2020 执行", bm=1)]
    assert not is_no_hit(hits, "折标系数依据 GB/T 2589 吗", min_cosine=0.35)


def test_identifier_absent_is_still_no_hit():
    assert is_no_hit([_hit(0.20, "P01 P02 装置", bm=1)], "P07 装置的开车率", min_cosine=0.35)


def test_common_words_do_not_count():
    """「怎么」「上报」这种二元组太容易撞上，不在术语表里就不能凭它判定找到了。
    （原来这里用的是"人工成本怎么算"，但知识库明确写了"人工成本暂不支持"——
      那道题的正确行为是引用这条说明，不是判无命中。旧测试的预期是错的。）"""
    hits = [_hit(0.19, "装置停车后怎么上报，见第七条", bm=1)]
    assert is_no_hit(hits, "安全事故怎么上报", min_cosine=0.30, glossary=GLOSSARY)


# ---- 术语字面命中（kb/glossary.txt） ----

GLOSSARY = ("利润", "成本", "人工成本", "回款率")


def test_glossary_term_rescues_low_cosine():
    """K13：「利润怎么算」和"暂不支持的指标"语义不像（余弦 0.22），但字面上说的就是它。"""
    hits = [_hit(0.22, "成本、利润、人工成本目前没有数据源", bm=1)]
    assert not is_no_hit(hits, "利润怎么算？", min_cosine=0.30, glossary=GLOSSARY)
    assert hit_signals(hits, "利润怎么算？", min_cosine=0.30, glossary=GLOSSARY) == ["术语"]


def test_glossary_term_must_appear_in_results_too():
    hits = [_hit(0.20, "开车率按日历天数计算", bm=1)]
    assert is_no_hit(hits, "利润怎么算", min_cosine=0.30, glossary=GLOSSARY)


def test_signals_report_every_reason():
    hits = [_hit(0.62, "回款率 = 回款 / 应收，P02 见附表", bm=1)]
    assert hit_signals(hits, "P02 的回款率", min_cosine=0.30, glossary=GLOSSARY) == \
        ["余弦", "标识符", "术语"]


def test_load_glossary_skips_comments_and_blanks(tmp_path):
    f = tmp_path / "g.txt"
    f.write_text("# 注释\n\n利润\n  回款率  \n", encoding="utf-8")
    assert load_glossary(f) == ("利润", "回款率")
    assert load_glossary(tmp_path / "nope.txt") == ()


def test_repo_glossary_has_no_filler_words():
    """防手滑：术语表里混进「怎么」这种词，辞职信也会被判成找到了。"""
    terms = load_glossary()
    assert {"利润", "回款率", "单耗"} <= set(terms)
    assert not {"怎么", "什么", "安全", "审批", "生产"} & set(terms)


def test_empty_is_no_hit():
    assert is_no_hit([], "能耗怎么算")


def test_default_threshold_comes_from_settings():
    assert is_no_hit([_hit(0.29)], "能耗怎么算")          # 默认 0.30（第 5 步标定）
    assert not is_no_hit([_hit(0.31)], "能耗怎么算")
