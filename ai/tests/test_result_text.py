from datetime import date
from decimal import Decimal

import pytest

from chemq.result_text import cell_to_text, golden_row, row_text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (Decimal("12.50"), "12.50"),
        (Decimal("1E+3"), "1000"),
        (Decimal("1E-7"), "0.0000001"),
        (date(2026, 8, 1), "2026-08-01"),
        (50, "50"),
        (0, "0"),
    ],
)
def test_cell_to_text(value, expected):
    assert cell_to_text(value) == expected


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"answer": 50}, {"v": "50"}),
        (
            {"period": 3, "price": Decimal("12.50")},
            {"k": "3", "v": "12.50"},
        ),
        ({"node": "node-a", "day": None}, {"k": "node-a", "v": ""}),
        ({"first": 1, "second": 2, "third": 3}, {"v": "1"}),
    ],
)
def test_golden_row(row, expected):
    assert golden_row(row) == expected


def test_skip_null_preserves_zero_and_empty_string():
    row = {"node": "node-a", "day": None, "count": 0, "label": ""}

    assert row_text(row) == ["node-a", "", "0", ""]
    assert row_text(row, skip_null=True) == ["node-a", "0", ""]