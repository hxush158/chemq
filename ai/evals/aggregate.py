#!/usr/bin/env python3
"""把多次评测报告聚合成「逐题通过率」—— 对抗采样噪声的主要工具。

为什么需要它：

这些模型上 temperature 已经不能设了（Sonnet 5 系列传非默认值直接 400），
所以同一份代码每次跑出来的 SQL 都可能不同。本项目实测过：
代码一个字没改，exec_acc 三次是 0.80 / 1.00 / 0.73 —— 噪声带 ±0.14。
而一次 prompt 改动的真实效果通常只有 0.05 量级。

**噪声比效果大三倍，看单次总分必然得出错误结论。**

逐题通过率把 11 道题 × 3 次 = 33 个观测按题聚合，信号清楚得多，而且不用多花钱。
上一轮的实例：总分从 0.843 掉到 0.788（看起来改坏了），但逐题看是
「4 道好了、2 道坏了」—— 两条新规则各自都命中了目标，只是又打坏了别的。
这个结论只有按题看才拿得到。

用法:
    # 一组报告：逐题通过率 + 总分均值/范围
    uv run python evals/aggregate.py ../runs/after-*.json

    # 两组对比：哪些题好了、哪些坏了、改动幅度有没有超过噪声带
    uv run python evals/aggregate.py ../runs/before-*.json --vs ../runs/after-*.json

    # 从仓库根目录：make agg  /  make agg-vs
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

PASS, FAIL, ERROR, ABSENT = "pass", "fail", "error", "absent"


class Group:
    """一组报告（同一份配置跑 N 次）。"""

    def __init__(self, paths: list[Path], name: str) -> None:
        self.name = name
        self.paths = paths
        self.reports: list[dict[str, Any]] = []
        for p in paths:
            try:
                self.reports.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception as e:  # noqa: BLE001
                raise SystemExit(f"读不了 {p}: {type(e).__name__}: {e}") from e
        if not self.reports:
            raise SystemExit(f"{name}: 没有可读的报告")

    # ---------- 逐题 ----------
    def status(self, rep: dict, qid: str) -> str:
        """一道题在一次运行里的状态。

        三态而不是两态：异常（跑不起来）和答错是两回事 ——
        异常不进 exec_acc 的分母，会让总分虚高，必须单独看见。
        """
        errs = {f["id"] for f in rep.get("failures", []) if "error" in f}
        if qid in errs:
            return ERROR
        case = rep.get("cases", {}).get(qid)
        if case is None:
            return ABSENT
        return PASS if case.get("assert_ok") else FAIL

    def all_ids(self) -> list[str]:
        ids: set[str] = set()
        for rep in self.reports:
            ids |= set(rep.get("cases", {}))
            ids |= {f["id"] for f in rep.get("failures", [])}
        return sorted(ids)

    def tally(self, qid: str) -> dict[str, int]:
        out = {PASS: 0, FAIL: 0, ERROR: 0, ABSENT: 0}
        for rep in self.reports:
            out[self.status(rep, qid)] += 1
        return out

    def n_runs(self, qid: str) -> int:
        """这道题参与了几次运行（排除本周还没纳入的）。"""
        return sum(1 for rep in self.reports if self.status(rep, qid) != ABSENT)

    # ---------- 总分 ----------
    def metric(self, key: str) -> list[float]:
        vals = []
        for rep in self.reports:
            v = rep.get("summary", {}).get(key)
            if isinstance(v, (int, float)) and v == v:   # v == v 过滤 NaN
                vals.append(float(v))
        return vals

    def band(self, key: str) -> tuple[float, float, float, float] | None:
        """均值、最小、最大、极差。极差就是噪声带 —— 改动幅度要和它比。"""
        v = self.metric(key)
        if not v:
            return None
        return statistics.fmean(v), min(v), max(v), max(v) - min(v)

    def cost(self) -> float:
        return sum(self.metric("cost_usd"))

    def n_errors(self) -> int:
        return sum(int(r.get("summary", {}).get("n_errors", 0) or 0) for r in self.reports)

    def last_reason(self, qid: str) -> str:
        """最近一次失败的原因，一行。"""
        for rep in reversed(self.reports):
            for f in rep.get("failures", []):
                if f["id"] != qid:
                    continue
                if "error" in f:
                    return "异常: " + str(f["error"]).split("(", 1)[0].strip()
                exp, got = f.get("expected_intent"), f.get("got_intent")
                if exp and got and exp != got:
                    return f"intent 判错: {exp} -> {got}"
                if not f.get("sql"):
                    return "没生成 SQL（被判拒答了）"
                return "结果与标准答案不一致"
        return ""


# ---------- 输出 ----------

def _fmt(x: float | None, nd: int = 4) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def show_one(g: Group) -> None:
    weeks = {r.get("config", {}).get("week") for r in g.reports}
    print(f"\n=== {g.name} · {len(g.reports)} 次运行 · week {sorted(w for w in weeks if w)} ===")
    for key, label in (("exec_acc", "exec_acc"), ("refuse_acc", "refuse_acc"),
                       ("intent_acc", "intent_acc"), ("citation_acc", "citation_acc")):
        b = g.band(key)
        if b is None:
            print(f"  {label:<13} —（本周没有计分的题）")
            continue
        mean, lo, hi, rng = b
        tail = f"  噪声带 ±{rng / 2:.3f}" if rng else "  （三次一致）"
        print(f"  {label:<13} 均值 {mean:.4f}   范围 {lo:.4f} ~ {hi:.4f}{tail}")
    print(f"  {'成本':<13} 合计 ${g.cost():.4f}   单次 ${g.cost() / len(g.reports):.4f}")
    print(f"  {'异常':<13} {g.n_errors()} 次" + ("   ⚠️ 异常不进 exec_acc 分母，总分偏乐观"
                                                 if g.n_errors() else ""))

    rows = []
    for qid in g.all_ids():
        n = g.n_runs(qid)
        if not n:
            continue
        t = g.tally(qid)
        if t[PASS] == n:
            continue
        rows.append((qid, t[PASS], n, t[ERROR], g.last_reason(qid)))

    print(f"\n  不是全过的题（{len(rows)} 道）")
    if not rows:
        print("    全过 ✓")
        return
    print(f"    {'题':<7}{'通过':>7}  {'异常':>4}  原因（最近一次）")
    for qid, p, n, e, why in rows:
        flag = "🔴" if p == 0 else ("🟡" if p < n else "  ")
        print(f"  {flag}{qid:<7}{f'{p}/{n}':>7}  {e:>4}  {why}")


def show_compare(a: Group, b: Group) -> None:
    show_one(a)
    show_one(b)

    print(f"\n=== 逐题对比：{a.name} → {b.name} ===")
    if len(a.reports) != len(b.reports):
        print(f"  ⚠️ 两组次数不同（{len(a.reports)} vs {len(b.reports)}），通过率不能直接比大小；"
              f"\n     下面的判定已经把「一次翻面」当成噪声扣掉了。")

    ids = sorted(set(a.all_ids()) | set(b.all_ids()))
    better, worse, unclear = [], [], []
    for qid in ids:
        na, nb = a.n_runs(qid), b.n_runs(qid)
        if not na or not nb:
            continue
        pa, pb = a.tally(qid)[PASS], b.tally(qid)[PASS]
        ra, rb = pa / na, pb / nb
        # 一道题在一组里翻一次面，通过率就变动 1/n。所以小于等于「一次翻面」
        # 的差异说明不了任何事 —— 2/2 → 2/3 不是"变差"，是样本不够。
        # 这是本脚本最容易写错的地方：直接比 rate 会把噪声报成结论。
        one_flip = 1.0 / max(na, nb)
        if abs(rb - ra) <= one_flip + 1e-9:
            if pa < na or pb < nb:
                unclear.append((qid, pa, na, pb, nb))
        elif rb > ra:
            better.append((qid, pa, na, pb, nb))
        else:
            worse.append((qid, pa, na, pb, nb))

    def dump(title: str, rows: list, mark: str) -> None:
        if not rows:
            return
        print(f"\n  {title}")
        for qid, pa, na, pb, nb in rows:
            print(f"    {mark} {qid}   {pa}/{na}  →  {pb}/{nb}")

    dump("确实好了（超过一次翻面）", better, "✅")
    dump("确实变差（超过一次翻面）", worse, "🔴")
    dump("说不清（差异在一次翻面之内，样本不够）", unclear, "？")
    if not (better or worse or unclear):
        print("    两组都全过 ✓")

    # ---------- 信号还是噪声 ----------
    ba, bb = a.band("exec_acc"), b.band("exec_acc")
    print("\n=== 这次改动算不算有效 ===")
    if not (ba and bb):
        print("  exec_acc 缺数据，无法判断")
        return
    delta = bb[0] - ba[0]
    noise = max(ba[3], bb[3]) / 2          # 用两组里更大的极差的一半当噪声带
    print(f"  exec_acc 均值  {ba[0]:.4f}  →  {bb[0]:.4f}   ({delta:+.4f})")
    print(f"  噪声带        ±{noise:.4f}（两组极差里取大的一半）")
    print()
    if abs(delta) <= noise:
        print(f"  ⚠️  改动幅度 {abs(delta):.4f} 没超过噪声带 {noise:.4f} ——")
        print("      总分不足以判定好坏，**以上逐题对比为准**。")
        print("      想让总分可信：加跑次数，或者把评测题目数做上去")
        print("      （现在一道题翻面就是 9 个百分点）。")
    elif delta > 0:
        print(f"  ✅ 提升 {delta:.4f} 超过噪声带 {noise:.4f}，可以认为真的变好了。")
    else:
        print(f"  🔴 下降 {abs(delta):.4f} 超过噪声带 {noise:.4f}，这是真退化，建议回滚。")


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="aggregate",
        description="把多次评测报告聚合成逐题通过率（对抗采样噪声）",
    )
    ap.add_argument("reports", nargs="+", type=Path, help="一组报告，例如 ../runs/after-*.json")
    ap.add_argument("--vs", nargs="+", type=Path, metavar="REPORT",
                    help="第二组报告，给出则做对比")
    ap.add_argument("--name", default="A", help="第一组的名字（默认 A）")
    ap.add_argument("--vs-name", default="B", help="第二组的名字（默认 B）")
    args = ap.parse_args()

    missing = [p for p in list(args.reports) + list(args.vs or []) if not p.is_file()]
    if missing:
        raise SystemExit("这些报告不存在：" + ", ".join(str(p) for p in missing))

    a = Group(sorted(args.reports), args.name)
    if args.vs:
        show_compare(a, Group(sorted(args.vs), args.vs_name))
    else:
        show_one(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
