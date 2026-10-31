"""生成目录和评测答案时，将数据库值转换为稳定的文本。"""

from decimal import Decimal
from typing import Any


def cell_to_text(value: Any) -> str:
    """将数据库单元格值转换为稳定的文本表示。"""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)

def row_text(row: dict[str, Any], *, skip_null: bool = False) -> list[str]:
    return [
        cell_to_text(value)
        for value in row.values()
        if not skip_null or value is not None
    ]

def golden_row(row: dict[str, Any]) -> dict[str, str]:
    parts = row_text(row)
    if len(parts) == 2:
        return {"k": parts[0], "v": parts[1]}
    return {"v": parts[0]}
