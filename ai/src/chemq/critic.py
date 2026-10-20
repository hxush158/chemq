"""W4：Critic —— 核对回答里的每个数字，都真的出现在工具返回的数据里。

用代码核对，不用大模型（W4 第 3 步的决定）：
  · 核对数字是机械活：提取数字 → 在数据里找 → 允许四舍五入。代码做这个不花钱、每次结果一样、能写单测。
  · 能用约束就别用建议：让大模型"检查一下数字对不对"，它会倾向于维护自己（或同类）刚说过的话。

"数据"指三样东西：SQL 返回的每一个单元格、知识库引用原文里的数字、用户问题里自己说的数字。
不在这三样里的数字 = 模型自己算的或编的 → 标 needs_human_review，列出是哪几个。

已知局限（刻意不处理，写清楚比假装处理好）：
  · 一位数的整数（排名"第 1"、"3 个装置"）默认不核对，太容易误报；
    但后面紧跟单位的（"3 次"、"5%"）照样核对。
  · 不理解语义：数字对、但张冠李戴（把 A 装置的数说成 B 装置的），代码查不出来。
  · 两期之差算核对通过（"环比下降 53.98 个百分点" = 两个数据点相减）。
    比例、合计不放行 —— 这些要求在 SQL 里算。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

# 先去掉日期，免得 2026、09、30 被当成要核对的数字
_DATE = re.compile(
    r"\d{4}\s*[-/年.]\s*\d{1,2}(\s*[-/月.]\s*\d{1,2}\s*日?)?"   # 2026-03-01 / 2026年3月5日 / 2026-03
    r"|\d{4}\s*年"                                             # 2026 年
    r"|\d{1,2}\s*月(\s*\d{1,2}\s*日)?"                          # 3 月 / 3月5日
    r"|\d{1,2}\s*日"
    r"|[一二三四]?季度|Q[1-4]"
)
# 资料编号 [KB-ENE-2026#7] 不是数值（agents.py 要求回答里标出处）
_REF = re.compile(r"\[?[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+#\d+\]?")
# 字母后面紧跟的数字是编号（P01、GB/T 2589、Q3），不是数值
_NUM = re.compile(r"(?<![A-Za-z/_\d.])-?\d[\d,]*(?:\.\d+)?")
_UNIT_AFTER = re.compile(r"^\s*(次|个|台|家|吨|t|%|％|小时|天|笔|位|kWh|元)")


@dataclass
class Verdict:
    ok: bool
    checked: list[str] = field(default_factory=list)       # 核对过的数字
    unverified: list[str] = field(default_factory=list)    # 在数据里找不到的数字


def _to_decimal(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def numbers_in_text(text: str) -> list[str]:
    """回答里需要核对的数字（原样字符串，保留小数位数，用来决定四舍五入到哪一位）。"""
    text = _DATE.sub(" ", _REF.sub(" ", text))
    out = []
    for m in _NUM.finditer(text):
        s = m.group()
        digits = s.lstrip("-").replace(",", "")
        if "." not in digits and len(digits) == 1 and not _UNIT_AFTER.match(text[m.end():]):
            continue                     # 一位整数且后面没单位：多半是排名、序号
        out.append(s)
    return out


def source_numbers(rows: Iterable[dict[str, Any]], texts: Iterable[str] = ()) -> list[Decimal]:
    vals: list[Decimal] = []
    for row in rows:
        for v in row.values():
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float, Decimal)):
                d = _to_decimal(str(v))
            else:
                d = _to_decimal(str(v)) if _NUM.fullmatch(str(v).strip()) else None
            if d is not None:
                vals.append(d)
    for t in texts:
        vals += [d for s in _NUM.findall(t) if (d := _to_decimal(s)) is not None]
    return vals


def _matches(claim: str, sources: list[Decimal]) -> bool:
    c = _to_decimal(claim)
    if c is None:
        return True
    places = len(claim.split(".")[1]) if "." in claim else 0
    q = Decimal(1).scaleb(-places)                     # 0.01 / 1
    for s in sources:
        if s == c or s.quantize(q) == c or abs(s) == abs(c):
            return True
        # 回答里写的是取整后的整数（"约 74179 吨"）
        if places == 0 and abs(s - c) < 1:
            return True
    return False


MAX_PAIR_SOURCES = 300            # 两两相减是 O(n²)，数据点太多时只看前 300 个不同的值


def _is_difference(claim: str, sources: list[Decimal]) -> bool:
    c = _to_decimal(claim)
    if c is None:
        return False
    places = len(claim.split(".")[1]) if "." in claim else 0
    q = Decimal(1).scaleb(-places)
    uniq = list(dict.fromkeys(sources))[:MAX_PAIR_SOURCES]
    target = abs(c)
    for i, a in enumerate(uniq):
        for b in uniq[i + 1:]:
            if abs(a - b).quantize(q) == target:
                return True
    return False


def check(answer: str, rows: Iterable[dict[str, Any]], texts: Iterable[str] = ()) -> Verdict:
    rows = list(rows)
    texts = list(texts)
    sources = source_numbers(rows, texts)
    claims = numbers_in_text(answer)
    bad = [c for c in claims if not _matches(c, sources) and not _is_difference(c, sources)]
    return Verdict(ok=not bad, checked=claims, unverified=bad)
