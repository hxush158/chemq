import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import run_eval  # noqa: E402
from run_eval import Report, num_close, rows_match  # noqa: E402


def test_no_citation_cases_is_not_unscored():
    """没有引用题，不应报引用指标未计分。"""
    rep = Report()

    assert "citation" not in rep.unscored_due(
        3, {"refuse": 0, "sql": 0, "citation": 0}
    )


def test_citation_cases_without_scores_is_unscored():
    """有引用题但没有计分，应报引用指标未计分。"""
    rep = Report()

    assert "citation" in rep.unscored_due(
        3, {"refuse": 0, "sql": 0, "citation": 2}
    )


@pytest.mark.parametrize("expected, actual, typed, ok", [
    ("21818516933", "21818516933", True, True),
    ("21818516933", "21818516934", True, False),
    ("21818516933", "21818516.933", True, False),
    ("83", "83.0", True, True),
    ("36.59", "36.594", True, True),
    ("36.59", "36.60", True, True),
    ("36.59", "36.61", True, False),
    ("-3.25", "-3.26", True, True),
    ("36.59", "36.61", False, True),
    # 整数不能经 float 丢精度；规则必须看标准答案的写法。
    ("9007199254740992", "9007199254740993", True, False),
    ("100.00", "100.4", True, False),
    ("100.000", "100.4", True, True),
    ("100.000", "100.6", True, False),
    # typed 的整数零严格相等；更多位小数和旧模式保留原来的零值规则。
    ("0", "0.0000000001", True, False),
    ("0.00", "0.01", True, True),
    ("0.000", "0.0000000001", True, True),
    ("0", "0.0000000001", False, True),
    ("2026-08-15", " 2026-08-15 ", True, True),
    ("node-a", "node-b", True, False),
])
def test_num_close_tolerance(expected, actual, typed, ok):
    assert num_close(actual, expected, typed=typed) is ok


@pytest.mark.parametrize("typed, ok", [(True, False), (False, True)])
def test_rows_match_passes_tolerance_with_unordered_rows(typed, ok):
    expected = [{"k": "a", "v": "36.59"}, {"k": "b", "v": "83"}]
    actual = [{"label": "b", "value": 83.0}, {"label": "a", "value": 36.61}]

    assert rows_match(actual, expected, typed=typed) is ok


def test_default_tolerance_keeps_legacy_behavior():
    assert num_close("36.61", "36.59")
    assert rows_match([{"price": 36.61}], [{"v": "36.59"}])


@pytest.mark.parametrize("typed, hits", [(True, 0), (False, 1)])
def test_evaluate_uses_tolerance_without_calling_model(monkeypatch, typed, hits):
    from chemq import pipeline

    result = SimpleNamespace(intent="metric_query", rows=[{"price": 36.61}], cost_usd=0)
    monkeypatch.setattr(pipeline, "answer", lambda *args, **kwargs: result)
    case = {
        "id": "TEST", "question": "test", "intent": "metric_query",
        "expected": [{"v": "36.59"}],
    }
    rep = Report()

    run_eval.evaluate(case, 2, rep, dry=False, typed=typed)

    assert rep.execution.total == 1
    assert rep.execution.hits == hits


@pytest.mark.parametrize("config, expected_typed", [
    ({"eval": {"tolerance": "typed"}}, True),
    ({"eval": {}}, False),
    ({}, False),
])
def test_main_reads_tolerance_and_passes_it_to_dry_check(monkeypatch, config, expected_typed):
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--week", "6", "--dry"])
    monkeypatch.setattr(run_eval, "domain", lambda: SimpleNamespace(id="test", config=config))
    monkeypatch.setattr(run_eval, "settings", lambda: SimpleNamespace(sql_dialect="test"))
    case = {"id": "TEST", "question": "test", "expected": [{"v": "36.59"}]}
    monkeypatch.setattr(run_eval, "load_cases", lambda *args, **kwargs: [case])
    seen = []

    def record_rows_match(actual, expected, typed=False):
        seen.append(typed)
        return rows_match(actual, expected, typed=typed)

    monkeypatch.setattr(run_eval, "rows_match", record_rows_match)

    assert run_eval.main() == 0
    assert seen == [expected_typed]

def test_contains_all_accepts_empty_list():
    assert run_eval.contains_all("任意文字", []) is True


@pytest.mark.parametrize("source", ["golden", "holdout", "external"])
def test_report_records_loaded_file_version(monkeypatch, tmp_path, source):
    monkeypatch.chdir(tmp_path)
    golden = tmp_path / "golden.jsonl"
    holdout = tmp_path / "holdout.jsonl"
    external = tmp_path / "external cases.jsonl"
    monkeypatch.setattr(run_eval, "GOLDEN", golden)
    monkeypatch.setattr(run_eval, "HOLDOUT", holdout)
    selected = {"golden": golden, "holdout": holdout, "external": external}[source]
    case = {
        "id": "TEST", "week": 1, "intent": "out_of_scope",
        "question": "测试文件来源", "assert_type": "refuse",
    }
    original = (json.dumps(case, ensure_ascii=False) + "\r\n").encode("utf-8")
    selected.write_bytes(original)
    report_path = tmp_path / "report.json"
    argv = ["run_eval.py", "--week", "1", "--json", str(report_path)]
    if source != "golden":
        argv += ["--set", "holdout"]
    if source == "external":
        argv += ["--file", external.name]
    monkeypatch.setattr(sys, "argv", argv)

    def fake_evaluate(case, week, rep, dry, engine, typed=False):
        assert case["id"] == "TEST"
        rep.intent.add(True)
        rep.refusal.add(True)
        # 模拟评测开始后源文件被修改；报告仍应记录读入时的版本。
        selected.write_text("changed during evaluation\n", encoding="utf-8")
        return "✓"

    monkeypatch.setattr(run_eval, "evaluate", fake_evaluate)

    assert run_eval.main() == 0
    config = json.loads(report_path.read_text(encoding="utf-8"))["config"]
    assert config["cases_file"] == str(selected.resolve())
    assert config["cases_sha256"] == hashlib.sha256(original).hexdigest()
