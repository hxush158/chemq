"""时间护栏：闭区间边界、两期完整性、配置错误和原计划不变。"""
from datetime import date
from types import SimpleNamespace

import pytest

from chemq import coverage, pipeline
from chemq.schemas import QueryPlan, TimeRange

COVERAGE = (date(2026, 1, 1), date(2026, 9, 30))


def make_plan(start="2026-08-01", end="2026-08-31", **extra):
    return QueryPlan(
        intent="metric_query",
        time_range=TimeRange(start=start, end=end, grain="day"),
        needs_knowledge=False,
        reasoning="测试时间覆盖",
        **extra,
    )


@pytest.mark.parametrize("start,end,action,expected", [
    ("2026-08-01", "2026-08-31", "ok", None),
    ("2026-01-01", "2026-09-30", "ok", None),
    ("2026-01-01", "2026-01-01", "ok", None),
    ("2026-09-30", "2026-09-30", "ok", None),
    ("2025-12-01", "2025-12-31", "refuse", None),
    ("2026-10-01", "2026-10-31", "refuse", None),
    ("2025-12-01", "2026-01-01", "clip", ("2026-01-01", "2026-01-01")),
    ("2026-09-30", "2026-10-31", "clip", ("2026-09-30", "2026-09-30")),
    ("2025-12-01", "2026-02-28", "clip", ("2026-01-01", "2026-02-28")),
    ("2026-08-01", "2026-10-31", "clip", ("2026-08-01", "2026-09-30")),
    ("2025-01-01", "2027-12-31", "clip", ("2026-01-01", "2026-09-30")),
    ("2026-02-30", "2026-03-31", "refuse", None),
    ("2026-08-01", "not-a-date", "refuse", None),
    ("2026-08-31", "2026-08-01", "refuse", None),
])
def test_time_boundaries_and_original_unchanged(start, end, action, expected):
    plan = make_plan(start, end)
    before = plan.model_dump()
    verdict = coverage.check_time(plan, COVERAGE)
    assert verdict.action == action
    assert plan.model_dump() == before
    if expected is not None:
        assert verdict.plan is not None
        assert verdict.plan is not plan
        assert verdict.plan.time_range is not None
        assert verdict.plan.time_range is not plan.time_range
        assert (verdict.plan.time_range.start, verdict.plan.time_range.end) == expected
        assert verdict.plan.time_range.grain == "day"
        assert verdict.plan.model_dump(exclude={"time_range"}) == plan.model_dump(exclude={"time_range"})
    else:
        assert verdict.plan is plan
    assert (verdict.reason is None) == (action == "ok")


@pytest.mark.parametrize("start,end,action", [
    ("2026-07-01", "2026-07-31", "ok"),
    ("2026-01-01", "2026-09-30", "ok"),
    ("2025-08-01", "2025-08-31", "refuse"),
    ("2025-12-01", "2026-01-31", "refuse"),
    ("2026-09-01", "2026-10-31", "refuse"),
    ("2026-10-01", "2026-10-31", "refuse"),
    ("2026-02-30", "2026-03-31", "refuse"),
    ("2026-07-31", "2026-07-01", "refuse"),
])
def test_comparison_requires_complete_valid_range(start, end, action):
    plan = make_plan(compare_range=TimeRange(start=start, end=end))
    verdict = coverage.check_time(plan, COVERAGE)
    assert verdict.action == action
    assert verdict.plan is plan
    if action == "refuse":
        assert "对比期" in verdict.reason


def test_comparison_does_not_clip_main_period():
    plan = make_plan("2026-09-01", "2026-10-31", compare_range=TimeRange(
        start="2026-07-01", end="2026-08-31",
    ))
    verdict = coverage.check_time(plan, COVERAGE)
    assert verdict.action == "refuse"
    assert "本期" in verdict.reason
    assert plan.time_range.end == "2026-10-31"


@pytest.mark.parametrize("intent", ["knowledge_lookup", "chitchat", "out_of_scope"])
def test_non_numeric_intents_bypass_time_check(intent):
    plan = QueryPlan(intent=intent, needs_knowledge=False, reasoning="无需取数")
    assert coverage.check_time(plan, COVERAGE).action == "ok"


@pytest.mark.parametrize("intent", ["metric_query", "mixed"])
def test_numeric_intents_require_main_period(intent):
    plan = QueryPlan(intent=intent, needs_knowledge=False, reasoning="缺失时间")
    assert coverage.check_time(plan, COVERAGE).action == "refuse"


def test_mixed_query_checks_time():
    plan = make_plan("2025-01-01", "2025-12-31").model_copy(update={"intent": "mixed"})
    assert coverage.check_time(plan, COVERAGE).action == "refuse"


def test_invalid_coverage_is_configuration_error():
    with pytest.raises(ValueError, match="数据覆盖"):
        coverage.check_time(make_plan(), (COVERAGE[1], COVERAGE[0]))


@pytest.fixture
def coverage_path(tmp_path, monkeypatch):
    path = tmp_path / "coverage.yaml"
    monkeypatch.setattr(coverage, "domain", lambda: SimpleNamespace(coverage_path=path))
    return path


def test_load_coverage_converts_strings_to_dates(coverage_path):
    coverage_path.write_text("start: '2026-01-01'\nend: '2026-09-30'\n", encoding="utf-8")
    assert coverage.load_coverage() == COVERAGE


def test_load_missing_coverage(coverage_path):
    with pytest.raises(FileNotFoundError, match="make catalog"):
        coverage.load_coverage()


@pytest.mark.parametrize("text", [
    "",
    "- '2026-01-01'",
    "start: '2026-01-01'",
    "start: null\nend: '2026-09-30'",
    "start: '2026-02-30'\nend: '2026-09-30'",
    "start: '2026-10-01'\nend: '2026-09-30'",
])
def test_load_invalid_coverage(coverage_path, text):
    coverage_path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        coverage.load_coverage()


@pytest.mark.parametrize("intent", ["metric_query", "mixed"])
@pytest.mark.parametrize("start,end,compare", [
    pytest.param("2026-01-01", "2026-09-30", ("2025-01-01", "2025-09-30"), id="Q010"),
    pytest.param("2025-06-01", "2025-06-30", None, id="H11"),
])
def test_pipeline_time_refusal_never_calls_text2sql(monkeypatch, intent, start, end, compare):
    """第 11 个用例：真正经过时间检查；拒答后不能继续生成 SQL 或检索知识库。"""
    plan = QueryPlan(
        intent=intent,
        time_range=TimeRange(start=start, end=end),
        compare_range=TimeRange(start=compare[0], end=compare[1]) if compare else None,
        needs_knowledge=intent == "mixed",
        reasoning="只提取时间，覆盖范围交给代码判断",
    )
    before = plan.model_dump()

    def fake_router(question):
        return SimpleNamespace(parsed=plan, usage=SimpleNamespace(cost_usd=0.001))

    def forbidden_text2sql(*args, **kwargs):
        pytest.fail("时间检查已拒答，不应再调用 text2sql")

    def forbidden_kb(*args, **kwargs):
        pytest.fail("时间检查已拒答，不应再调用知识库")

    # pipeline 直接导入了这些函数，所以要替换 pipeline 中使用的名字。
    monkeypatch.setattr(pipeline, "plan", fake_router)
    monkeypatch.setattr(
        pipeline, "load_coverage", lambda: (date(2025, 10, 1), date(2026, 9, 30)),
    )
    monkeypatch.setattr(pipeline, "generate_and_run", forbidden_text2sql)
    monkeypatch.setattr(pipeline, "answer_from_kb", forbidden_kb)

    result = pipeline.answer("测试时间覆盖拒答")

    assert result.refused is True
    assert result.intent == "out_of_scope"
    assert "对比期" in result.answer if compare else "没有交集" in result.answer
    assert result.caveats == ["时间超出覆盖（代码判定）"]
    assert result.sql is None and result.rows == []
    assert result.cost_usd == pytest.approx(0.001)  # 只产生 Router 成本
    assert plan.model_dump() == before
