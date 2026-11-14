import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

import sqlglot

from chemq import sqlguard
from chemq.db import engine
from chemq.domain import domain
from chemq.result_text import golden_row


def load_cases(path: Path) -> list[dict]:
    cases = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            case = json.loads(line)

            if not isinstance(case, dict):
                raise ValueError(f"第{line_number} 行必须是题目对象")

            if not case.get("id"):
                raise ValueError(f"第{line_number} 行缺少题目 id")

            cases.append(case)

        if not cases:
            raise ValueError("标准答案文件中没有题目")
        
    return cases

def select_sql(case: dict, source_dialect:str, target_dialect:str) -> str:
    override_sql = f"sql_{target_dialect}"

    if override_sql in case:
        sql = case[override_sql]

        if not isinstance(sql, str) or not sql.strip():
            raise ValueError(f"{override_sql} 必须是非空SQL字符串")

        return sql

    statements = sqlglot.transpile(
        case["reference_sql"],
        read=source_dialect,
        write=target_dialect,
        unsupported_level=sqlglot.ErrorLevel.RAISE,
    )

    if len(statements) != 1 or not statements[0].strip():
        raise ValueError("参考 SQL 必须翻译成一条非空语句")

    return statements[0]

def canonical(value):
    """数值只统一写法：去掉尾随的 0（Snowflake 的 NUMBER 带固定小数位，18884.722 会写成 18884.722000）。
    不放宽数值：18884.7221 和 18884.722 仍然不同。日期、文字等不是数字的原样返回。"""
    if not isinstance(value, str):
        return value
    try:
        number = Decimal(value)
    except InvalidOperation:
        return value
    if not number.is_finite():
        return value
    text = format(number.normalize(), "f")
    return "0" if text == "-0" else text


def canonical_rows(rows: list[dict]) -> list[dict]:
    return [{key: canonical(value) for key, value in row.items()} for row in rows]


def sorted_rows(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
        ),
    )

def main() -> int:
    d = domain()
    target_engine = engine()
    target_dialect = target_engine.dialect

    # TODO：当前方言等于主方言时，打印错误并 return 1。
    if target_dialect == d.primary_dialect:
        print("Error: Target dialect is the same as the main dialect.")
        return 1

    try:
        cases = load_cases(d.golden_path)
    except (OSError, ValueError) as exc:
        print(f"ERROR 无法读取标准答案：{exc}")
        return 1

    failures = 0
    # TODO：逐题比较，累计失败数。
    for case in cases:
        case_id = case["id"]

        if not case.get("reference_sql"):
            print(f"SKIP {case_id}")
            continue

        sql = None

        try:
            expected = case["expected"]

            sql = select_sql(
                case,
                source_dialect=d.primary_dialect,
                target_dialect=target_dialect,
            )

            verdict = sqlguard.check(
                sql,
                d.allowed_relations,
                dialect=target_dialect,
            )

            if not verdict.ok:
                raise ValueError(f"护栏拒绝：{verdict.violations}")

            actual = [
                golden_row(row)
                for row in target_engine.run_query(sql)
            ]

            same = sorted_rows(canonical_rows(expected)) == sorted_rows(canonical_rows(actual))

        except Exception as exc:
            failures += 1
            print(f"DIFF {case_id}，处理失败：{exc}")
            print("expected =", case.get("expected"))
            print("actual = 未能完成查询或比较")
            print("reference_sql =", case["reference_sql"])
            print("target_sql =", sql)
            continue

        if same:
            print(f"SAME {case_id}")
        else:
            failures += 1
            print(f"DIFF {case_id}")
            print("expected =", expected)
            print("actual =", actual)
            print("target_sql =", sql)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())