"""第 6 步：带引用的回答。全部用假的检索 / 假的 Claude，不连数据库、不调 API。"""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from chemq import kb_answer as ka
from chemq import pipeline
from chemq.rag import Hit
from chemq.schemas import QueryPlan

HITS = [
    Hit(chunk_id=11, doc_id="KB-MET-2026", title="经营指标口径说明",
        content="《经营指标口径说明》 › 暂不支持的指标\n成本、利润、人工成本目前没有数据源。",
        rrf=0.03, cos_sim=0.22, vec_rank=3, bm_rank=1),
    Hit(chunk_id=7, doc_id="KB-ENE-2026", title="能耗考核管理办法",
        content="《能耗考核管理办法》 › 第三章 考核 › 第八条 计划检修期间的能耗\n第一段。\n\n第二段。",
        rrf=0.02, cos_sim=0.55, vec_rank=1, bm_rank=2),
]


class FakeEmbedder:
    def embed(self, text):
        return NS(vector=[0.0] * 4)


class FakeLlm:
    def __init__(self, content, cost=0.01):
        self.content, self.cost, self.calls = content, cost, []

    def complete(self, **kw):
        self.calls.append(kw)
        return NS(content=self.content, usage=NS(cost_usd=self.cost))


class NoLlm:
    def complete(self, **kw):
        raise AssertionError("无命中时不该调 Claude")


def text(t, *idx, cited="原文"):
    cites = [NS(type="search_result_location", search_result_index=i, cited_text=cited) for i in idx]
    return NS(type="text", text=t, citations=cites or None)


def run(content, hits=HITS, question="利润怎么算？", llm=None, **kw):
    llm = llm or FakeLlm(content)
    return ka.answer_from_kb(question, embedder=FakeEmbedder(), llm=llm,
                             search=lambda q, emb: list(hits), **kw), llm


# ---------- 组装请求 ----------

def test_search_result_blocks_shape():
    blocks = ka.to_search_results(HITS)
    b = blocks[1]
    assert b["type"] == "search_result" and b["citations"] == {"enabled": True}
    assert b["source"] == "kb://KB-ENE-2026#7"
    assert b["title"].startswith("《能耗考核管理办法》 › 第三章")
    assert [c["text"] for c in b["content"]] == ["第一段。", "第二段。"]   # 按段拆，引用才精确
    assert all(c["text"] for blk in blocks for c in blk["content"])      # 空 text 块 API 会拒


def test_user_content_puts_question_last_and_rows_as_plain_text():
    blocks = ka.build_user_content("3月单耗为什么高？", HITS, sql_rows=[{"k": "2026-03", "v": 134.75}])
    assert blocks[-1]["text"] == "用户问题：3月单耗为什么高？"
    assert blocks[-2]["type"] == "text" and "134.75" in blocks[-2]["text"]   # 不是 search_result，不能被引用
    assert sum(b["type"] == "search_result" for b in blocks) == 2


# ---------- 解析回答 ----------

def test_parse_answer_maps_index_to_hit_and_dedupes():
    content = [text("利润暂不支持。", 0, cited="成本、利润"), text("另见", 0, 1),
               NS(type="thinking", text="x")]
    ans, cites = ka.parse_answer(content, HITS)
    assert ans == "利润暂不支持。另见"
    assert [c.chunk_id for c in cites] == [11, 7]
    assert cites[0].snippet == "成本、利润"


def test_parse_answer_ignores_out_of_range_index():
    _, cites = ka.parse_answer([text("x", 5)], HITS)
    assert cites == []


# ---------- 三个出口 ----------

def test_no_hit_returns_without_calling_claude():
    low = [Hit(chunk_id=1, doc_id="D", title="t", content="《t》 › a\n毫不相干",
               rrf=0.01, cos_sim=0.1, vec_rank=1, bm_rank=None)]
    r, _ = run(None, hits=low, question="帮我写一封辞职信", llm=NoLlm())
    assert r.refused and r.kind == "no_hit" and r.cost_usd == 0


def test_not_covered_marker_means_refused():
    r, _ = run([text("【资料未覆盖】资料只有考核口径，没有奖惩标准。", 1)])
    assert r.refused and r.kind == "not_covered"
    assert "【资料未覆盖】" not in r.answer and "奖惩" in r.answer
    assert r.cost_usd == 0.01                                  # 拒答也要记成本


def test_answer_without_citation_is_blocked():
    r, _ = run([text("利润 = 收入 - 成本。")])                 # 凭常识答的，没有出处
    assert r.refused and r.kind == "no_citation"
    assert "利润 = 收入" not in r.answer


def test_grounded_answer_passes():
    r, llm = run([text("利润目前暂不支持。", 0)])
    assert not r.refused and r.kind is None
    assert r.citations[0].doc_id == "KB-MET-2026"
    assert llm.calls[0]["system"] == ka.SYSTEM_PROMPT
    assert llm.calls[0]["max_tokens"] == 1024


# ---------- 防泄题 ----------

def test_system_prompt_does_not_leak_near_miss_questions():
    """W2 的教训：举例里出现评测题原话，分数就是虚的。"""
    golden = Path(__file__).resolve().parents[1] / "evals" / "kb_golden.jsonl"
    near = [json.loads(x) for x in golden.read_text(encoding="utf-8").splitlines() if x.strip()]
    near = [g for g in near if g["kind"] == "near_miss"]
    assert near
    for phrase in ["奖金", "4 月", "4月", "停止发货", "安全检查", "P01"]:
        assert phrase not in ka.SYSTEM_PROMPT, phrase
    for g in near:
        assert g["question"] not in ka.SYSTEM_PROMPT


# ---------- pipeline 分流 ----------

def _plan(intent):
    qp = QueryPlan(intent=intent, needs_knowledge=intent != "metric_query", reasoning="r")
    return NS(parsed=qp, usage=NS(cost_usd=0.001))


def test_pipeline_knowledge_lookup_uses_kb(monkeypatch):
    monkeypatch.setattr(pipeline, "plan", lambda q: _plan("knowledge_lookup"))
    monkeypatch.setattr(pipeline, "answer_from_kb", lambda q, sql_rows=None: ka.KbResult(
        answer="利润暂不支持。", refused=False,
        citations=[ka.Citation(doc_id="D", title="t", chunk_id=1, snippet="s")], cost_usd=0.02))
    a = pipeline.answer("利润怎么算？")
    assert a.answer == "利润暂不支持。" and not a.refused and len(a.citations) == 1
    assert a.cost_usd == pytest.approx(0.021)


def test_pipeline_knowledge_refusal_propagates(monkeypatch):
    monkeypatch.setattr(pipeline, "plan", lambda q: _plan("knowledge_lookup"))
    monkeypatch.setattr(pipeline, "answer_from_kb", lambda q, sql_rows=None: ka.KbResult(
        answer="没找到", refused=True, kind="no_hit"))
    a = pipeline.answer("帮我写辞职信")
    assert a.refused and "no_hit" in a.caveats[0]


def test_pipeline_mixed_passes_rows_and_keeps_numbers_when_kb_fails(monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline, "plan", lambda q: _plan("mixed"))
    monkeypatch.setattr(pipeline, "generate_and_run", lambda *a, **k: NS(
        sql="select 1", rows=[{"v": 134.75}], cost_usd=0.03))

    def fake_kb(q, sql_rows=None):
        seen["rows"] = sql_rows
        return ka.KbResult(answer="资料只有…", refused=True, kind="not_covered", cost_usd=0.01)

    monkeypatch.setattr(pipeline, "answer_from_kb", fake_kb)
    a = pipeline.answer("3月单耗多少，为什么高？")
    assert seen["rows"] == [{"v": 134.75}]                    # SQL 结果交给了 Claude
    assert not a.refused and a.rows == [{"v": 134.75}]         # 数字还在，不算整体拒答
    assert "not_covered" in a.caveats[0]
    assert a.cost_usd == pytest.approx(0.041)
