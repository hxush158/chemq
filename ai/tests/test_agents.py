"""分析员 Agent：不真的调 Claude。测的是我们自己写的那部分 ——
工具包装、hooks、Recorder → Answer 的转换、拒答约定、外部上限。"""
import asyncio
import json
from pathlib import Path

import pytest

from chemq import agents
from chemq.agents import CANNOT_ANSWER, Recorder, to_answer

GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "golden.jsonl"


def _rec(sql_rows=None, kb=None, tools=()):
    r = Recorder()
    if sql_rows is not None:
        r.sql_runs.append({"ok": True, "sql": "SELECT 1 FROM chem.v_plant_daily LIMIT 500", "rows": sql_rows})
    r.kb_hits = kb or []
    r.audit = [{"id": str(i), "tool": f"mcp__chemq__{t}", "input": {}, "allowed": True} for i, t in enumerate(tools)]
    return r


def test_refusal_marker():
    a = to_answer(f"{CANNOT_ANSWER}系统没有利润数据。", _rec(), cost=0.01, stop="success")
    assert a.refused and a.intent == "out_of_scope" and a.answer == "系统没有利润数据。"
    assert a.rows == [] and a.citations == []


HIT8 = {"ref": "KB-ENE-2026#7", "doc_id": "KB-ENE-2026", "chunk_id": 7,
        "source": "《办法》 › 第八条", "text": "检修月单耗不作为考核依据"}
HIT1 = {"ref": "KB-ENE-2026#0", "doc_id": "KB-ENE-2026", "chunk_id": 0,
        "source": "《办法》 › 第一条 目的", "text": "为规范能耗考核制定本办法"}


def test_intent_inferred_from_tools_used():
    rows = [{"v": "134.75"}]
    assert to_answer("134.75", _rec(rows, tools=["run_semantic_query"]), cost=0, stop="success").intent == "metric_query"
    k = to_answer("检修月不考核 [KB-ENE-2026#7]", _rec(None, [HIT8], ["search_knowledge"]), cost=0, stop="success")
    assert k.intent == "knowledge_lookup" and not k.refused
    a = to_answer("134.75，检修月不考核 [KB-ENE-2026#7]",
                  _rec(rows, [HIT8], ["run_semantic_query", "search_knowledge"]), cost=0, stop="success")
    assert a.intent == "mixed" and a.citations[0].title == "《办法》 › 第八条"


def test_only_marked_hits_become_citations():
    """检索到 2 条，回答只标了第八条 → 引用只有第八条（第一版把检索到的全算成了引用）。"""
    a = to_answer("检修月不考核 [KB-ENE-2026#7]", _rec(None, [HIT8, HIT1], ["search_knowledge"]), cost=0, stop="success")
    assert [c.chunk_id for c in a.citations] == [7]


def test_knowledge_answer_without_refs_is_blocked():
    a = to_answer("检修月不考核", _rec(None, [HIT8], ["search_knowledge"]), cost=0, stop="success")
    assert a.refused and "no_citation" in a.caveats[0] and "检修月不考核" not in a.answer


def test_mixed_answer_without_refs_keeps_numbers_with_caveat():
    a = to_answer("3 月 134.75，因为大修", _rec([{"v": "134.75"}], [HIT8], ["run_semantic_query", "search_knowledge"]),
                  cost=0, stop="success")
    assert not a.refused and a.rows == [{"v": "134.75"}] and "未经引用核实" in a.caveats[0]


def test_critic_flags_number_not_in_data():
    a = to_answer("8 月总产量 78000 吨", _rec([{"v": "74178.7"}], tools=["run_semantic_query"]), cost=0, stop="success")
    assert a.needs_human_review and not a.numbers_from_sql and "78000" in a.caveats[0]


def test_hitting_max_turns_is_not_an_answer():
    a = to_answer("先查一下……", _rec(), cost=0.09, stop="error_max_turns")
    assert a.refused and "error_max_turns" in a.caveats[0]


def test_rows_come_from_last_successful_query():
    r = _rec([{"v": 1}])
    r.sql_runs.append({"ok": False, "stage": "guard", "violations": ["x"]})
    r.sql_runs.append({"ok": True, "sql": "SELECT 2", "rows": [{"v": 2}]})
    assert to_answer("2", r, cost=0, stop="success").rows == [{"v": 2}]


def test_prompt_does_not_leak_golden_questions():
    qs = [json.loads(x)["question"] for x in GOLDEN.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert not [q for q in qs if q in agents.SYSTEM_PROMPT]


# ---------- 需要 claude_agent_sdk（make install-agent）----------

def test_tool_wrapper_binds_region_and_marks_errors(monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    seen = {}

    def fake_run(sql, *, region=None):
        seen["region"] = region
        return {"ok": False, "stage": "guard", "violations": ["不允许访问 chem.fact_sales"]}

    monkeypatch.setattr(agents.tools, "run_semantic_query", fake_run)
    rec = Recorder()
    tools = {t.name: t for t in agents.build_tools(rec, region="华南")}
    assert set(tools) == set(agents.TOOL_NAMES)
    out = asyncio.run(tools["run_semantic_query"].handler({"sql": "SELECT * FROM chem.fact_sales"}))
    assert out["is_error"] is True and "fact_sales" in out["content"][0]["text"]
    assert seen["region"] == "华南" and rec.sql_runs[0]["ok"] is False


def test_pre_hook_denies_tools_outside_whitelist():
    pytest.importorskip("claude_agent_sdk")
    rec = Recorder()
    hooks = agents.make_hooks(rec)
    pre = hooks["PreToolUse"][0].hooks[0]
    post = hooks["PostToolUse"][0].hooks[0]

    denied = asyncio.run(pre({"tool_name": "Bash", "tool_input": {"command": "psql"}}, "t1", None))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"

    ok = asyncio.run(pre({"tool_name": "mcp__chemq__list_metrics", "tool_input": {}}, "t2", None))
    asyncio.run(post({"tool_name": "mcp__chemq__list_metrics"}, "t2", None))
    assert ok == {}
    assert [a["allowed"] for a in rec.audit] == [False, True] and "ms" in rec.audit[1]


def test_output_contract_lives_in_the_tool_description():
    """输出约定写在工具说明里，换调用方（Claude Code CLI、别的 Agent）也能看到。"""
    from chemq import tools
    for phrase in ("YYYY-MM", "LIMIT 1", "两期的原值各一行"):
        assert phrase in tools.DESC_RUN_SQL
    assert "先用 SQL 核实这个数" in agents.SYSTEM_PROMPT


def test_external_limits_are_set():
    assert agents.MAX_TURNS <= 10 and agents.MAX_BUDGET_USD <= 0.10
