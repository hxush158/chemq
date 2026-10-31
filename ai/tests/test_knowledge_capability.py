"""领域知识库开关：配置、实际工具注册、hook 和固定流程的边界。"""
import json
import os
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from chemq import domain as domain_module
from chemq import pipeline, prompts
from chemq.domain import load_domain
from chemq.schemas import QueryPlan, SqlCandidate, TimeRange
from chemq.text2sql import SqlRun, Unanswerable


@pytest.mark.parametrize("domain_id, expected", [("chem", True), ("nz_power", False)])
def test_pack_declares_knowledge_capability(domain_id, expected):
    assert load_domain(domain_id).has_kb is expected


@pytest.mark.parametrize("declaration", ["", 'knowledge_base: "false"', "knowledge_base: 0"])
def test_missing_or_non_boolean_capability_rejected(tmp_path, monkeypatch, declaration):
    root = tmp_path / "example"
    root.mkdir()
    (root / "domain.yaml").write_text(f"id: example\n{declaration}\n", encoding="utf-8")
    monkeypatch.setattr(domain_module, "DOMAINS_DIR", tmp_path)
    with pytest.raises(ValueError, match="knowledge_base"):
        load_domain("example")


def test_enabled_kb_requires_a_directory(tmp_path, monkeypatch):
    root = tmp_path / "example"
    root.mkdir()
    config = root / "domain.yaml"
    config.write_text("id: example\nknowledge_base: true\n", encoding="utf-8")
    monkeypatch.setattr(domain_module, "DOMAINS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="知识库"):
        load_domain("example")
    docs = root / "kb" / "docs"
    docs.mkdir(parents=True)
    assert load_domain("example").has_kb
    config.write_text("id: example\nknowledge_base: false\n", encoding="utf-8")
    assert not load_domain("example").has_kb  # 有目录也不能覆盖显式的 false


@pytest.mark.parametrize("domain_id, dialect", [("chem", "postgres"), ("nz_power", "duckdb")])
def test_registered_tools_and_hook_in_fresh_process(domain_id, dialect):
    pytest.importorskip("claude_agent_sdk")
    pytest.importorskip("mcp")
    script = '''
import asyncio
import json
from chemq import agents, mcp_server
from chemq.domain import domain

rec = agents.Recorder()
pre = agents.make_hooks(rec)["PreToolUse"][0].hooks[0]
decision = asyncio.run(pre(
    {"tool_name": "mcp__chemq__search_knowledge", "tool_input": {}}, "call", None))
server = mcp_server.build_server()
print(json.dumps({
    "has_kb": domain().has_kb,
    "names": list(agents.TOOL_NAMES),
    "allowed": list(agents.ALLOWED),
    "agent_tools": [t.name for t in agents.build_tools(rec, None)],
    "mcp_tools": [t.name for t in asyncio.run(server.list_tools())],
    "prompt": agents.SYSTEM_PROMPT,
    "instructions": mcp_server.INSTRUCTIONS,
    "decision": decision,
}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script], text=True, capture_output=True, timeout=30,
        env={**os.environ, "CHEMQ_DOMAIN": domain_id, "CHEMQ_SQL_DIALECT": dialect},
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    expected = {"list_metrics", "run_semantic_query"}
    if data["has_kb"]:
        expected.add("search_knowledge")
    for key in ("names", "agent_tools", "mcp_tools"):
        assert set(data[key]) == expected
    assert set(data["allowed"]) == {f"mcp__chemq__{name}" for name in expected}
    for key in ("prompt", "instructions"):
        assert ("search_knowledge" in data[key]) == data["has_kb"]
    if data["has_kb"]:
        assert data["decision"] == {}
    else:
        assert data["decision"]["hookSpecificOutput"]["permissionDecision"] == "deny"


def _pipeline_stubs(monkeypatch, domain_id, intent):
    d = load_domain(domain_id)
    monkeypatch.setattr(pipeline, "domain", lambda: d)
    monkeypatch.setattr(prompts, "domain", lambda: d)
    plan = QueryPlan(
        intent=intent, needs_knowledge=True, reasoning="test",
        time_range=TimeRange(start="2026-08-01", end="2026-08-31"),
    )
    monkeypatch.setattr(pipeline, "plan", lambda _: NS(parsed=plan, usage=NS(cost_usd=0.01)))
    monkeypatch.setattr(pipeline, "load_coverage", lambda: None)
    monkeypatch.setattr(pipeline, "check_time", lambda *a: NS(action="allow", plan=plan))
    sql = Mock(return_value=SqlRun(
        sql="test SQL", rows=[{"v": 12.5}], candidate=SqlCandidate(sql="test SQL"), cost_usd=0.02,
    ))
    monkeypatch.setattr(pipeline, "generate_and_run", sql)
    return sql


@pytest.mark.parametrize("intent", ["knowledge_lookup", "mixed"])
def test_nz_pipeline_never_calls_kb(monkeypatch, intent):
    sql = _pipeline_stubs(monkeypatch, "nz_power", intent)
    kb = Mock(side_effect=AssertionError("NZ 不应调用知识库"))
    monkeypatch.setattr(pipeline, "answer_from_kb", kb)
    answer = pipeline.answer("why?")
    kb.assert_not_called()
    assert answer.intent == intent
    assert not answer.citations
    if intent == "knowledge_lookup":
        sql.assert_not_called()
        assert answer.refused and not answer.numbers_from_sql
        assert answer.answer == prompts.string("kb_unavailable")
        assert answer.cost_usd == pytest.approx(0.01)
    else:
        sql.assert_called_once()
        assert not answer.refused and answer.numbers_from_sql
        assert answer.sql == "test SQL" and answer.rows == [{"v": 12.5}]
        assert answer.cost_usd == pytest.approx(0.03)
        assert answer.caveats == [prompts.string("kb_explanation_unavailable")]


def test_nz_mixed_preserves_sql_refusal(monkeypatch):
    sql = _pipeline_stubs(monkeypatch, "nz_power", "mixed")
    sql.side_effect = Unanswerable("unavailable metric", kind="model_refused", cost_usd=0.02)
    kb = Mock(side_effect=AssertionError("NZ 不应调用知识库"))
    monkeypatch.setattr(pipeline, "answer_from_kb", kb)
    answer = pipeline.answer("why?")
    assert answer.refused and answer.answer == "unavailable metric"
    assert answer.cost_usd == pytest.approx(0.03)
    kb.assert_not_called()


def test_nz_mixed_preserves_time_refusal(monkeypatch):
    sql = _pipeline_stubs(monkeypatch, "nz_power", "mixed")
    monkeypatch.setattr(pipeline, "check_time", lambda *a: NS(action="refuse", reason="outside coverage"))
    kb = Mock(side_effect=AssertionError("NZ 不应调用知识库"))
    monkeypatch.setattr(pipeline, "answer_from_kb", kb)
    answer = pipeline.answer("why?")
    assert answer.refused and answer.answer == "outside coverage"
    assert answer.cost_usd == pytest.approx(0.01)
    sql.assert_not_called()
    kb.assert_not_called()


@pytest.mark.parametrize("intent", ["knowledge_lookup", "mixed"])
def test_chem_pipeline_still_calls_kb(monkeypatch, intent):
    _pipeline_stubs(monkeypatch, "chem", intent)
    kb = Mock(return_value=NS(answer="grounded answer", cost_usd=0.04, refused=False, citations=[]))
    monkeypatch.setattr(pipeline, "answer_from_kb", kb)
    answer = pipeline.answer("why?")
    kb.assert_called_once_with("why?", sql_rows=[{"v": 12.5}] if intent == "mixed" else None)
    assert not answer.refused and answer.answer == "grounded answer"
    assert answer.cost_usd == pytest.approx(0.07 if intent == "mixed" else 0.05)
