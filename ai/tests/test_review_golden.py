"""口径送审表（ai/evals/review_golden.py）：内容对、该拦的拦住。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import review_golden as rg  # noqa: E402

CASES = [
    {"id": "N1", "question": "q1", "method": "对 SI 的行直接平均"},
    {"id": "N2", "question": "q2", "method": "不涉及数据", "assert_type": "refuse"},
]
GOLDEN = [
    {"id": "N1", "question": "q1", "assert_type": "sql",
     "reference_sql": "SELECT secret_sql", "expected": [{"k": str(i), "v": "1.00"} for i in range(7)]},
    {"id": "N2", "question": "q2", "assert_type": "refuse"},
]


def test_render_has_question_method_head_rows_and_total_but_no_sql():
    text = rg.render("nz_power", CASES, GOLDEN)
    assert "**题目**：q1" in text and "对 SI 的行直接平均" in text
    assert "共 7 行（只列前 5 行）" in text
    assert "| 4 | 1.00 |" in text and "| 5 | 1.00 |" not in text
    assert "**期望行为**：拒答" in text
    assert "secret_sql" not in text          # 不放参考 SQL


def test_missing_method_is_rejected():
    cases = [dict(CASES[0], method=" "), CASES[1]]
    with pytest.raises(ValueError, match="N1"):
        rg.check_in_sync(cases, GOLDEN)


def test_cases_and_golden_out_of_sync_is_rejected():
    with pytest.raises(RuntimeError, match="make golden"):
        rg.check_in_sync(CASES[:1], GOLDEN)
