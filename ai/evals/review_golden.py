#!/usr/bin/env python3
"""生成 golden 题的口径送审表（Markdown），给数据底座的同事只从口径角度审参考 SQL。

每题一节：题目原文、一句话算法说明（cases.yaml 的 method）、期望行为，
数值题再附标准答案的前几行和总行数。不放参考 SQL（审的人只看口径，不看代码），
也不放 holdout（holdout 要保持独立，不给任何人提前看）。

标准答案取自 golden.jsonl（make golden 生成的），算法说明取自 cases.yaml。
golden.jsonl 和 cases.yaml 对不上（改了题没重跑 make golden），或者有题没写 method，都直接报错。

用法: make review-golden DOMAIN=nz_power
     输出 domains/<领域>/eval/review_golden.md（.gitignore 忽略，不进仓库）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_golden as bg  # noqa: E402

from chemq.domain import domain  # noqa: E402

HEAD_ROWS = 5
EXPECTED_BEHAVIOUR = {
    "refuse": "拒答",
    "no_data": "回答“这段时间没有数据”（结果 0 行，不算拒答）",
    "citation": "用指标字典的条目回答，并标出引用",
}


def load_golden(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"{path} 不存在：先 make golden")
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def check_in_sync(cases: list[dict], golden: list[dict]) -> None:
    want = [c["id"] for c in cases]
    got = [g["id"] for g in golden]
    if want != got:
        raise RuntimeError(f"golden.jsonl 和 cases.yaml 的题目对不上：先 make golden\n  cases: {want}\n  golden: {got}")
    missing = [c["id"] for c in cases if not str(c.get("method", "")).strip()]
    if missing:
        raise ValueError(f"这些题没有写 method（一句话算法说明）：{missing}")


def _table(rows: list[dict]) -> list[str]:
    cols = list(dict.fromkeys(k for r in rows for k in r))
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows[:HEAD_ROWS]]
    return out


def render(domain_id: str, cases: list[dict], golden: list[dict]) -> str:
    by_id = {c["id"]: c for c in cases}
    out = [
        f"# 口径送审：{domain_id} golden（{len(golden)} 题）",
        "",
        "请只从口径角度看：算法说明和标准答案是否符合指标字典。参考 SQL 和 holdout 题不在这份表里。",
        "",
    ]
    for g in golden:
        c = by_id[g["id"]]
        out += [f"## {g['id']}", "", f"**题目**：{g['question']}", "", f"**算法**：{c['method'].strip()}", ""]
        kind = g.get("assert_type", "sql")
        if kind in EXPECTED_BEHAVIOUR:
            out += [f"**期望行为**：{EXPECTED_BEHAVIOUR[kind]}", ""]
        rows = g.get("expected")
        if rows is not None:
            shown = min(len(rows), HEAD_ROWS)
            out += [f"**标准答案**：共 {len(rows)} 行" + (f"（只列前 {shown} 行）" if len(rows) > shown else ""), ""]
            out += _table(rows) if rows else ["（0 行）"]
            out.append("")
    return "\n".join(out)


def main() -> None:
    d = domain()
    cases = bg.load_cases(d.eval_dir / "cases.yaml")["golden"]
    golden = load_golden(d.golden_path)
    check_in_sync(cases, golden)
    path = d.eval_dir / "review_golden.md"
    path.write_text(render(d.id, cases, golden), encoding="utf-8")
    print(f"✓ 送审表已生成：{path}（{len(golden)} 题）")


if __name__ == "__main__":
    main()
