"""破坏实验 B 的篡改函数：篡改要真的改了数字，而且只改那一处。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import critic_lab as lab  # noqa: E402

ANS = "3 月单耗 134.75，2 月 130.32，高了 4.43"
ROWS = [{"k": "2026-02", "v": "130.32"}, {"k": "2026-03", "v": "134.75"}, {"k": "2026-01", "v": "131.00"}]


def test_mutations_change_exactly_one_number():
    muts = lab.mutations(ANS, ROWS, [])
    assert muts and all(m[1] != ANS for m in muts)
    m = next(m for m in muts if m[0] == "末位 +1" and m[2] == "134.75")
    assert m[3] == "134.76" and m[1] == "3 月单耗 134.76，2 月 130.32，高了 4.43"


def test_all_four_kinds_present():
    assert {m[0] for m in lab.mutations(ANS, ROWS, [])} == set(lab.KINDS)


def test_thousands_separator_kept():
    assert lab._fmt(lab.Decimal("74180.5"), "74,178.7") == "74,180.5"
