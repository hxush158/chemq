#!/usr/bin/env python3
"""破坏实验 B：往 Agent 的真实回答里塞假数字，看代码版 Critic 抓不抓得住。

    make critic-lab            # 第一次会让 Agent 答 6 道题（约 $0.1），结果缓存在 runs/
    make critic-lab REFRESH=1  # 重新让 Agent 答

对每个回答里的每个数字，做 4 种篡改，每种都用两种 Critic 设置各核对一次：
  末位 +1        134.75 → 134.76                    预期：抓住
  放大 3%        134.75 → 138.79                    预期：抓住
  换成另一个数据  把 3 月的数换成 2 月的数（张冠李戴）  预期：抓不住 —— 代码不懂语义，已知局限
  凑成两数之差    换成数据里某两个数相减的结果         预期：放行差值时抓不住 —— 量"放行差值"松了多少

另外看原始回答有没有被误报（误报 = Critic 把对的说成错的）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ai" / "src"))

from chemq import critic  # noqa: E402
from chemq.domain import domain

QUESTIONS = ["Q001", "Q004", "Q009", "Q011", "Q014", "Q015"]
CACHE = ROOT / "runs" / "critic_lab_cache.json"
KINDS = ["末位 +1", "放大 3%", "换成另一个数据", "凑成两数之差"]


def _fmt(d: Decimal, like: str) -> str:
    places = len(like.split(".")[1]) if "." in like else 0
    q = Decimal(1).scaleb(-places)
    s = f"{d.quantize(q):,f}" if "," in like else f"{d.quantize(q):f}"
    return s


def _replace(text: str, old: str, new: str) -> str:
    return re.sub(rf"(?<![\d.,]){re.escape(old)}(?![\d])", new, text, count=1)


def mutations(answer: str, rows: list[dict], texts: list[str]) -> list[tuple[str, str, str, str]]:
    """返回 [(篡改类型, 篡改后的回答, 原数字, 新数字)]。"""
    row_nums = list(dict.fromkeys(critic.source_numbers(rows)))
    out = []
    for claim in dict.fromkeys(critic.numbers_in_text(answer)):
        c = Decimal(claim.replace(",", ""))
        places = len(claim.split(".")[1]) if "." in claim else 0
        unit = Decimal(1).scaleb(-places)
        cands = {
            "末位 +1": c + unit,
            "放大 3%": c * Decimal("1.03"),
        }
        other = next((n for n in row_nums if n.quantize(unit) != c.quantize(unit) and n > 0), None)
        if other is not None:
            cands["换成另一个数据"] = other
        diff = next((abs(a - b) for i, a in enumerate(row_nums) for b in row_nums[i + 1:]
                     if abs(a - b).quantize(unit) not in (c.quantize(unit), 0)), None)
        if diff is not None:
            cands["凑成两数之差"] = diff
        for kind, val in cands.items():
            new = _fmt(val, claim)
            if new != claim:
                out.append((kind, _replace(answer, claim, new), claim, new))
    return out


async def _collect() -> list[dict]:
    from chemq.agents import orchestrate  # noqa: PLC0415

    gold = {json.loads(x)["id"]: json.loads(x)
            for x in domain().golden_path.read_text(encoding="utf-8").splitlines() if x.strip()}
    out = []
    for qid in QUESTIONS:
        run = await orchestrate(gold[qid]["question"], as_of="2026-09-14")
        out.append({"id": qid, "question": gold[qid]["question"], "answer": run.answer.answer,
                    "rows": run.rows_all, "texts": run.cited_texts, "cost": run.answer.cost_usd})
        print(f"  {qid} ${run.answer.cost_usd:.4f}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args(argv)

    if a.refresh or not CACHE.exists():
        print("让 Agent 答题（只在第一次或 --refresh 时花钱）：")
        data = asyncio.run(_collect())
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    else:
        data = json.loads(CACHE.read_text(encoding="utf-8"))

    false_alarm: dict[bool, list[str]] = {True: [], False: []}
    tally = {(k, d): [0, 0] for k in KINDS for d in (True, False)}     # (类型, 放行差值) -> [抓住, 总数]
    examples: dict[str, str] = {}
    for item in data:
        for allow in (True, False):
            v = critic.check(item["answer"], item["rows"], item["texts"],
                             question=item["question"], allow_diff=allow)
            if not v.ok:
                false_alarm[allow].append(f"{item['id']}: {v.unverified}")
        for kind, mutated, old, new in mutations(item["answer"], item["rows"], item["texts"]):
            for allow in (True, False):
                # 只算"被篡改的那个数"有没有被标出来；回答里别的数被标（误报）不算抓住
                caught = new in critic.check(mutated, item["rows"], item["texts"],
                                             question=item["question"], allow_diff=allow).unverified
                tally[(kind, allow)][0] += caught
                tally[(kind, allow)][1] += 1
                if not caught and allow and kind not in examples:
                    examples[kind] = f"{item['id']}  {old} → {new}"

    for allow, name in ((True, "放行差值（现行）"), (False, "不放行差值")):
        fa = false_alarm[allow]
        print(f"\n原始回答 {len(data)} 个，{name}时被标出 {len(fa)} 个（逐个看：是误报还是真错）"
              + (f"：{fa}" if fa else ""))
    print(f"\n{'篡改类型':<14}{'放行差值（现行）':>18}{'不放行差值':>14}")
    for k in KINDS:
        cells = []
        for allow in (True, False):
            c, n = tally[(k, allow)]
            cells.append(f"{c}/{n} 抓住" if n else "—")
        print(f"{k:<14}{cells[0]:>18}{cells[1]:>14}")
    if examples:
        print("\n没抓住的例子（现行设置）：")
        for k, e in examples.items():
            print(f"  {k}：{e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
