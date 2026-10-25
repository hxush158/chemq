#!/usr/bin/env python3
"""把领域包 eval/cases.yaml 里的题目列成一张表：一题一行，方便浏览。只读，不连数据库。

用法: make cases            全部
      make cases SET=holdout 只看一个题集
"""
import os
import sys
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "domains" / os.environ.get("CHEMQ_DOMAIN", "chem") / "eval" / "cases.yaml"


def _pad(text: str, width: int) -> str:
    """按显示宽度补空格：中文字占两格，直接用 :<6 会对不齐。"""
    w = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(width - w, 1)


def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8")) or []
    if only:
        cases = [c for c in cases if c.get("set") == only]
    print(f"{CASES.relative_to(ROOT)}（{len(cases)} 题）\n")
    print(_pad("编号", 6) + _pad("题集", 9) + _pad("意图", 18) + _pad("判分", 18) + "题目")
    for c in cases:
        kind = c.get("assert_type", "sql")
        print(f"{c['id']:<6}{c.get('set', ''):<9}{c.get('intent', ''):<18}{kind:<18}{c['question']}")


if __name__ == "__main__":
    main()
