"""评测题文件 eval/cases.yaml 和它生成的 golden.jsonl / holdout.jsonl 要保持同步。

改了 cases.yaml 却忘了 make golden，评测跑的就还是旧题 —— 这条测试提醒你。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import build_golden as bg  # noqa: E402

from chemq.domain import domain  # noqa: E402


def test_cases_file_loads_and_ids_are_unique():
    groups = bg.load_cases(domain().eval_dir / "cases.yaml")
    ids = [c["id"] for cs in groups.values() for c in cs]
    assert len(ids) == len(set(ids))
    assert groups["golden"], "golden 一道题都没有"


@pytest.mark.parametrize("name", ["golden", "holdout"])
def test_generated_jsonl_matches_cases(name):
    path = bg.SETS[name]
    if not path.exists():
        pytest.skip(f"{path.name} 还没生成：make golden")
    want = [c["id"] for c in bg.load_cases(domain().eval_dir / "cases.yaml")[name]]
    got = [json.loads(x)["id"] for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert got == want, f"{path.name} 和 cases.yaml 对不上：改了题目之后要 make golden"


def test_set_field_is_not_written_into_jsonl():
    for c in bg.load_cases(domain().eval_dir / "cases.yaml")["golden"]:
        assert "set" not in c


def test_bad_set_fails_loudly(tmp_path):
    p = tmp_path / "cases.yaml"
    p.write_text("- {id: X1, set: gold, question: q}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="set"):
        bg.load_cases(p)
