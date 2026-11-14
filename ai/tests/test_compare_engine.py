import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import compare_engine


@pytest.fixture
def comparison_setup(tmp_path, monkeypatch):
    golden_path = tmp_path / "golden.jsonl"

    case = {
        "id": "T001",
        "reference_sql": "SELECT price FROM mart.test_prices",
        "expected": [{"v": "45.02"}],
    }

    fake_domain = SimpleNamespace(
        golden_path=golden_path,
        primary_dialect="duckdb",
        allowed_relations=("mart.test_prices",),
    )

    fake_engine = SimpleNamespace(
        dialect="snowflake",
        run_query=Mock(
            return_value=[{"price": Decimal("45.02")}]
        ),
    )

    monkeypatch.setattr(compare_engine, "domain", lambda: fake_domain)
    monkeypatch.setattr(compare_engine, "engine", lambda: fake_engine)

    return SimpleNamespace(
        case=case,
        golden_path=golden_path,
        engine=fake_engine,
    )


def write_case(setup):
    setup.golden_path.write_text(
        json.dumps(setup.case, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

def test_same_result(comparison_setup, capsys):
    setup = comparison_setup
    write_case(setup)

    exit_code = compare_engine.main()

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "SAME T001" in output
    setup.engine.run_query.assert_called_once_with(
        "SELECT price FROM mart.test_prices"
    )

def test_different_result(comparison_setup, capsys):
    setup = comparison_setup
    setup.engine.run_query.return_value = [
        {"price": Decimal("46.02")}
    ]
    write_case(setup)

    exit_code = compare_engine.main()

    output = capsys.readouterr().out
    assert exit_code != 0
    assert "DIFF T001" in output
    assert "45.02" in output
    assert "46.02" in output
    assert "SELECT price FROM mart.test_prices" in output
    setup.engine.run_query.assert_called_once()

def test_override_sql_used(comparison_setup, monkeypatch, capsys):
    setup = comparison_setup
    override_sql = "SELECT price FROM mart.test_prices LIMIT 1"
    setup.case["sql_snowflake"] = override_sql
    write_case(setup)

    transpile = Mock(
        side_effect=AssertionError("有覆盖 SQL 时不应调用翻译")
    )
    monkeypatch.setattr(compare_engine.sqlglot, "transpile", transpile)

    exit_code = compare_engine.main()

    assert exit_code == 0
    assert "SAME T001" in capsys.readouterr().out
    transpile.assert_not_called()
    setup.engine.run_query.assert_called_once_with(override_sql)

def test_case_without_reference_sql_skipped(
    comparison_setup, monkeypatch, capsys
):
    setup = comparison_setup
    setup.case = {
        "id": "T002",
        "assert_type": "refuse",
    }
    write_case(setup)

    select_sql = Mock()
    monkeypatch.setattr(compare_engine, "select_sql", select_sql)

    exit_code = compare_engine.main()

    assert exit_code == 0
    assert "SKIP T002" in capsys.readouterr().out
    select_sql.assert_not_called()
    setup.engine.run_query.assert_not_called()

def test_primary_dialect_rejected(
    comparison_setup, monkeypatch, capsys
):
    setup = comparison_setup
    setup.engine.dialect = "duckdb"

    load_cases = Mock()
    monkeypatch.setattr(compare_engine, "load_cases", load_cases)

    exit_code = compare_engine.main()

    assert exit_code != 0
    assert "same as the main dialect" in capsys.readouterr().out
    load_cases.assert_not_called()
    setup.engine.run_query.assert_not_called()

def test_guard_rejection_prevents_query(comparison_setup, capsys):
    setup = comparison_setup
    setup.case["reference_sql"] = (
        "SELECT price FROM mart.private_prices"
    )
    write_case(setup)

    exit_code = compare_engine.main()

    output = capsys.readouterr().out
    assert exit_code != 0
    assert "DIFF T001" in output
    assert "护栏拒绝" in output
    setup.engine.run_query.assert_not_called()

@pytest.mark.parametrize("actual, same", [
    (Decimal("45.020000"), True),     # Snowflake NUMBER 的固定小数位：同一个数
    (Decimal("45.0201"), False),      # 真的不同，不能被"统一写法"吞掉
])
def test_trailing_zeros_are_formatting_not_difference(comparison_setup, capsys, actual, same):
    setup = comparison_setup
    setup.engine.run_query.return_value = [{"price": actual}]
    write_case(setup)

    exit_code = compare_engine.main()

    assert (exit_code == 0) is same
    assert ("SAME T001" in capsys.readouterr().out) is same


@pytest.mark.parametrize("value, expected", [
    ("18884.722000", "18884.722"), ("50", "50"), ("100.00", "100"),
    ("-0.00", "0"), ("2026-06-24", "2026-06-24"), ("Coal", "Coal"),
])
def test_canonical_only_changes_number_formatting(value, expected):
    assert compare_engine.canonical(value) == expected
