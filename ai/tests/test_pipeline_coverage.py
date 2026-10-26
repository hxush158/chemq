"""时间护栏的流程契约：拒答不取数，裁剪计划及提醒传到后续出口。"""
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from chemq import pipeline
from chemq.kb_answer import KbResult
from chemq.schemas import QueryPlan, TimeRange
from chemq.text2sql import Unanswerable


@pytest.fixture
def stages(monkeypatch):
    router = Mock()
    sql = Mock(return_value=NS(sql="test sql", rows=[{"v": 1}], cost_usd=0.02))
    kb = Mock(return_value=KbResult(answer="解释", refused=False, cost_usd=0.03))
    coverage = Mock(return_value=(date(2025, 10, 1), date(2026, 9, 30)))
    for name, mock in [
        ("plan", router), ("generate_and_run", sql),
        ("answer_from_kb", kb), ("load_coverage", coverage),
    ]:
        monkeypatch.setattr(pipeline, name, mock)
    return NS(router=router, sql=sql, kb=kb, coverage=coverage)


def set_plan(stages, start, end, *, intent="metric_query", compare=None):
    plan = QueryPlan(
        intent=intent, time_range=TimeRange(start=start, end=end),
        compare_range=TimeRange(start=compare[0], end=compare[1]) if compare else None,
        needs_knowledge=intent == "mixed", reasoning="时间提取",
    )
    stages.router.return_value = NS(parsed=plan, usage=NS(cost_usd=0.001))
    return plan


@pytest.mark.parametrize("intent,kb_refused", [
    ("metric_query", False), ("mixed", False), ("mixed", True),
])
def test_clipped_plan_and_warning_survive_success_paths(stages, intent, kb_refused):
    plan = set_plan(stages, "2025-09-01", "2025-11-30", intent=intent)
    before = plan.model_dump()
    stages.kb.return_value = KbResult(
        answer="知识库结果", refused=kb_refused,
        kind="no_hit" if kb_refused else None, cost_usd=0.03,
    )
    result = pipeline.answer("测试裁剪", region="test-region")
    sent_plan = stages.sql.call_args.args[0]
    assert sent_plan is not plan
    assert sent_plan.time_range.start == "2025-10-01"
    assert sent_plan.time_range.end == "2025-11-30"
    assert plan.model_dump() == before
    assert stages.sql.call_args.kwargs["region"] == "test-region"
    assert not result.refused and result.intent == intent
    assert "裁剪" in result.caveats[0]
    assert "2025-09-01" in result.caveats[0] and "2025-10-01" in result.caveats[0]
    assert result.rows == [{"v": 1}]
    if intent == "mixed":
        stages.kb.assert_called_once_with("测试裁剪", sql_rows=[{"v": 1}])
        assert result.cost_usd == pytest.approx(0.051)
        if kb_refused:
            assert "no_hit" in result.caveats[1]
    else:
        stages.kb.assert_not_called()
        assert result.cost_usd == pytest.approx(0.021)


def test_clipping_warning_survives_text2sql_refusal(stages):
    set_plan(stages, "2026-09-01", "2026-10-31")
    stages.sql.side_effect = Unanswerable("无法取数", kind="model_refused", cost_usd=0.02)
    result = pipeline.answer("测试裁剪后的拒答")
    assert result.refused and result.intent == "metric_query"
    assert "裁剪" in result.caveats[0] and "text2sql 拒答" in result.caveats[1]
    assert result.cost_usd == pytest.approx(0.021)
    stages.kb.assert_not_called()


def test_ok_plan_passes_through_unchanged(stages):
    plan = set_plan(stages, "2026-08-01", "2026-08-31")
    result = pipeline.answer("测试正常查询")
    assert stages.sql.call_args.args[0] is plan
    assert not result.refused and result.caveats == []


@pytest.mark.parametrize("intent", ["knowledge_lookup", "chitchat", "out_of_scope"])
def test_non_numeric_intents_do_not_load_coverage(stages, intent):
    stages.router.return_value = NS(
        parsed=QueryPlan(intent=intent, needs_knowledge=True, reasoning="非取数意图"),
        usage=NS(cost_usd=0.001),
    )
    result = pipeline.answer("测试非取数意图")
    stages.coverage.assert_not_called()
    stages.sql.assert_not_called()
    assert result.intent == intent
    if intent == "knowledge_lookup":
        stages.kb.assert_called_once()
    else:
        stages.kb.assert_not_called()
        assert result.refused
