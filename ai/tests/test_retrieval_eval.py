"""检索评测脚本里"判对"和"缓存"的部分 —— 判对写错了，Recall 整个是假的，所以单独测。"""
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import run_retrieval as rr  # noqa: E402


@dataclass
class H:
    doc_id: str
    content: str


def _c(path: str) -> str:
    return f"《某办法》 › {path}\n正文"


def test_heading_of():
    assert rr.heading_of(_c("第二章 计算方法 › 第三条 综合能耗的计算依据")) == \
        "第二章 计算方法 › 第三条 综合能耗的计算依据"
    assert rr.heading_of("没有前缀的正文") == ""


@pytest.mark.parametrize("path, section, ok", [
    ("第二章 计算方法 › 第三条 综合能耗的计算依据", "第三条", True),
    ("回款率", "回款率", True),
    ("第四章 附则 › 第十一条 施行", "第一条", False),      # 用"包含"会误中
    ("第一章 总则 › 第一条 目的", "第一条", True),
    ("单位产品综合能耗", "能耗", False),                   # 不能半截匹配
])
def test_matches_last_segment_only(path, section, ok):
    assert rr.matches("D", _c(path), {"doc_id": "D", "section": section}) is ok


def test_matches_requires_same_doc():
    assert not rr.matches("A", _c("回款率"), {"doc_id": "B", "section": "回款率"})


def test_score_rank_and_recall():
    hits = [H("D", _c("甲")), H("D", _c("乙")), H("D", _c("丙"))]
    rel = [{"doc_id": "D", "section": "乙"}, {"doc_id": "D", "section": "丙"},
           {"doc_id": "D", "section": "丁"}]
    m = rr.score(hits, rel)
    assert m.rank == 2 and m.found == 2 and m.n_rel == 3
    assert m.hit and m.recall == pytest.approx(2 / 3)


def test_score_miss():
    m = rr.score([H("D", _c("甲"))], [{"doc_id": "D", "section": "乙"}])
    assert m.rank is None and not m.hit and m.recall == 0


def test_embedding_cache_avoids_second_call(tmp_path):
    cache = tmp_path / "emb.json"
    calls = []

    def embed(q):
        calls.append(q)
        return [0.1, 0.2]

    rr.load_embeddings(["问1", "问2"], "m", embed, cache)
    rr.load_embeddings(["问1", "问2"], "m", embed, cache)
    assert calls == ["问1", "问2"]                     # 第二次全走缓存
    rr.load_embeddings(["问1"], "m2", embed, cache)    # 换模型要重算
    assert calls[-1] == "问1" and len(calls) == 3
    assert "m|问1" in json.loads(cache.read_text(encoding="utf-8"))


def test_summarize_excludes_no_hit_from_scores():
    def q(id_, kind, hit, cos):
        r = rr.QResult(id=id_, kind=kind, question="", best_cos=cos)
        for m in rr.MODES:
            r.modes[m] = rr.ModeResult(rank=1 if hit else None, found=int(hit), n_rel=0 if kind == "no_hit" else 1)
        return r

    s = rr.summarize([q("A", "fuzzy", True, 0.6), q("B", "fuzzy", False, 0.5), q("C", "no_hit", False, 0.2)])
    assert s["overall"]["hybrid"]["hit"] == 0.5 and s["overall"]["hybrid"]["n"] == 2
    assert s["cos_answerable"] == [0.5, 0.6] and s["cos_no_hit"] == [0.2]
