#!/usr/bin/env python3
"""对比两次评测，回答「准确率涨了 X%，成本涨了 Y 倍」。

    python ai/evals/compare.py A.json B.json

A 是基线，B 是新方案。输出三段：
  1. 指标对比 —— 每项的绝对值、变化量、变化倍数
  2. 翻转的题 —— 从对变错 / 从错变对，这比总分更有信息量
  3. 一句话结论 —— 值不值

为什么要专门写这个：靠肉眼 diff 两份 JSON，你只会看到总分的变化，
看不到「哪几题翻了」。而翻转的那几题才告诉你新方案改善了什么、破坏了什么 ——
总分持平也可能是「修好三题、弄坏三题」。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

RATE_KEYS = ["intent_acc", "exec_acc", "refuse_acc", "citation_acc"]


def pct(x) -> str:
    return "—" if x is None or x != x else f"{x * 100:.1f}%"


def delta_rate(a, b) -> str:
    if a is None or b is None or a != a or b != b:
        return "—"
    d = (b - a) * 100
    if abs(d) < 0.05:
        return "持平"
    return f"{d:+.1f} 个百分点"


def ratio(a: float, b: float) -> str:
    if not a:
        return "—" if not b else "基线为 0"
    r = b / a
    return f"×{r:.2f}" + ("  (更贵)" if r > 1.02 else "  (更省)" if r < 0.98 else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", type=Path, help="基线报告")
    ap.add_argument("new", type=Path, help="新方案报告")
    args = ap.parse_args()

    A = json.loads(args.base.read_text(encoding="utf-8"))
    B = json.loads(args.new.read_text(encoding="utf-8"))
    la, lb = A.get("label", args.base.stem), B.get("label", args.new.stem)
    sa, sb = A["summary"], B["summary"]

    print(f"\n基线 A = {la}")
    for k, v in A.get("config", {}).items():
        print(f"         {k}: {v}")
    print(f"新方案 B = {lb}")
    for k, v in B.get("config", {}).items():
        print(f"         {k}: {v}")

    print("\n" + "=" * 68)
    print(f"  {'指标':<14}{'A 基线':>12}{'B 新方案':>12}   变化")
    print("-" * 68)
    for k in RATE_KEYS:
        print(f"  {k:<14}{pct(sa.get(k)):>12}{pct(sb.get(k)):>12}   {delta_rate(sa.get(k), sb.get(k))}")
    print("-" * 68)
    ca, cb = sa.get("cost_usd", 0.0), sb.get("cost_usd", 0.0)
    n = sa.get("n_cases") or 1
    print(f"  {'总成本 USD':<14}{ca:>12.4f}{cb:>12.4f}   {ratio(ca, cb)}")
    print(f"  {'单题成本':<14}{ca / n:>12.5f}{cb / n:>12.5f}")
    pa, pb = sa.get("p95_ms", 0), sb.get("p95_ms", 0)
    print(f"  {'p95 延迟 ms':<14}{pa:>12}{pb:>12}   {ratio(pa, pb)}")
    print("=" * 68)

    # ---- 翻转的题 ----
    A_c, B_c = A.get("cases", {}), B.get("cases", {})
    fixed, broke = [], []
    for cid in sorted(set(A_c) | set(B_c)):
        a, b = A_c.get(cid), B_c.get(cid)
        if not a or not b:
            continue
        a_ok = a["intent_ok"] and a["assert_ok"]
        b_ok = b["intent_ok"] and b["assert_ok"]
        if not a_ok and b_ok:
            fixed.append(cid)
        elif a_ok and not b_ok:
            broke.append(cid)

    print(f"\n  修好了 {len(fixed)} 题: {', '.join(fixed) or '（无）'}")
    print(f"  弄坏了 {len(broke)} 题: {', '.join(broke) or '（无）'}")
    if broke:
        print("  ⚠ 总分可能持平，但有题从对变错 —— 这是只看总分会漏掉的信息")

    # ---- 结论 ----
    gain = (sb.get("intent_acc") or 0) - (sa.get("intent_acc") or 0)
    print("\n  " + "-" * 64)
    if ca and cb:
        print(f"  一句话：准确率 {gain * 100:+.1f} 个百分点，成本 {ratio(ca, cb).split()[0]}。")
        if gain > 0.02 and cb / ca < 1.5:
            print("  值 —— 准确率提升明显，成本涨幅可接受。")
        elif gain <= 0.005 and cb > ca:
            print("  不值 —— 准确率没动，钱白花了。")
        else:
            print("  自己判断 —— 把这个权衡和它的理由写进 docs/journal/w1.md。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
