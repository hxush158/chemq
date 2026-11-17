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


@pytest.mark.parametrize("result_kw, hits", [
    ({"rows": [], "no_data": True, "refused": False}, 1),
    ({"rows": [], "no_data": False, "refused": True}, 0),     # 把"没数据"当拒答：错
    ({"rows": [{"v": 0}], "no_data": False, "refused": False}, 0),  # 查出了数（比如 0）：错
])
def test_no_data_case_scores_execution(monkeypatch, result_kw, hits):
    from chemq import pipeline

    result = SimpleNamespace(intent="metric_query", cost_usd=0, **result_kw)
    monkeypatch.setattr(pipeline, "answer", lambda *args, **kwargs: result)
    case = {
        "id": "ND", "question": "test", "intent": "metric_query",
        "assert_type": "no_data",
    }
    rep = Report()

    run_eval.evaluate(case, 6, rep, dry=False)

    assert rep.execution.total == 1
    assert rep.execution.hits == hits
    assert rep.refusal.total == 0


def test_no_data_case_rejects_expected_rows():
    case = {
        "id": "ND", "week": 6, "intent": "metric_query", "question": "q",
        "assert_type": "no_data", "expected": [{"v": "1"}],
    }
    with pytest.raises(ValueError, match="no_data"):
        run_eval.validate_cases([case], "test.jsonl")


def test_sql_case_notes_must_contain_is_validated():
    case = {
        "id": "S", "week": 6, "intent": "metric_query", "question": "q",
        "assert_type": "sql", "expected": [{"v": "1"}],
        "notes_must_contain": "unweighted",          # 应为列表
    }
    with pytest.raises(ValueError, match="notes_must_contain"):
        run_eval.validate_cases([case], "test.jsonl")


CITE_NO_KEYWORDS = {
    "id": "C", "week": 1, "intent": "mixed", "question": "q",
    "assert_type": "sql_and_citation", "expected": [{"v": "1"}],
}


def test_repo_golden_citation_case_may_omit_must_contain(tmp_path):
    """chem Q009 / Q020 这类老题：没写 must_contain = 有引用即可。"""
    path = tmp_path / "golden.jsonl"
    path.write_text(json.dumps(CITE_NO_KEYWORDS), encoding="utf-8")
    assert run_eval.load_cases(6, path)[0]["id"] == "C"


@pytest.mark.parametrize("extra", [{}, {"must_contain": []}])
def test_external_file_citation_case_requires_keywords(tmp_path, extra):
    path = tmp_path / "holdout.jsonl"
    path.write_text(json.dumps({**CITE_NO_KEYWORDS, **extra}), encoding="utf-8")
    with pytest.raises(ValueError, match="must_contain"):
        run_eval.load_cases(6, path, strict_citation=True)


def test_repo_golden_files_pass_validation():
    """仓库里的评测集都要能加载 —— 这条测试缺了，11/14 chem 评测才在跑的时候才炸。"""
    from chemq.domain import available, load_domain
    for domain_id in available():
        d = load_domain(domain_id)
        for path in (d.golden_path, d.holdout_path):
            if path.exists():
                run_eval.load_cases(99, path)


def test_empty_case_set_is_an_error_not_a_pass(monkeypatch, tmp_path, capsys):
    """空的评测集（比如 NZ 仓库里的 holdout.jsonl）必须报错，不能打印"全部通过"。"""
    empty = tmp_path / "holdout.jsonl"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--week", "6", "--dry", "--file", str(empty)])
    monkeypatch.setattr(run_eval, "domain", lambda: SimpleNamespace(id="test", config={}))
    monkeypatch.setattr(run_eval, "settings", lambda: SimpleNamespace(sql_dialect="test"))
    assert run_eval.main() == 2
    assert "没有题" in capsys.readouterr().err
