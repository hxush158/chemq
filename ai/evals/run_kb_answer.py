#!/usr/bin/env python3
"""知识库问答评测 —— 检索 + Claude 回答 + 引用，端到端。

    make eval-kb            # 要 ANTHROPIC key；问题向量走 runs/ 缓存，缓存里没有的才调 Titan

直接调 kb_answer.answer_from_kb()，不经过 router —— 只测"有了正确意图之后，答得对不对"。
路由对不对是 make eval 的事。

kb_golden 的 20 道题分三类计分：
  有答案 13 道   引用正确率：没拒答，且引用里至少一条是标注的正确章节     目标 ≥ 0.90
                 误拒：被拒答                                              目标 ≤ 1
  无答案 3 道    拒答（应在检索层就挡住，不调 Claude）                    目标 3/3
  擦边题 4 道    拒答（Claude 以【资料未覆盖】开头）                       目标 ≥ 3/4

有大模型参与，结果会抖：按 W2 的规矩连跑 3 次再下结论（make runs-kb N=3，存到 runs/kb-*.json）。
退出码：0 达标；1 未达标；2 没跑成。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ai" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_retrieval as rr  # noqa: E402  复用：题目文件、判对、问题向量缓存

TARGET_CITE = 0.90
MAX_FALSE_REFUSE = 1
MIN_NEAR_MISS = 3


@dataclass
class Row:
    id: str
    kind: str
    question: str
    refused: bool
    refuse_kind: str | None
    cited: list[str] = field(default_factory=list)   # 被引用的章节路径
    cite_ok: bool | None = None                      # 只对有答案题有意义
    answer: str = ""
    cost_usd: float = 0.0

    @property
    def ok(self) -> bool:
        if self.kind in rr.NO_ANSWER:
            return self.refused
        return bool(self.cite_ok)


class CachedEmbedder:
    """问题向量用 run_retrieval 的缓存，重跑不调 AWS。"""

    def __init__(self, vectors: dict[str, list[float]]):
        self._v = vectors

    def embed(self, text: str):
        from chemq.embed import Embedding  # noqa: PLC0415
        return Embedding(vector=self._v[text], tokens=0)


def grade(g: dict[str, Any], kb: Any) -> Row:
    by_chunk = {h.chunk_id: h for h in kb.hits}
    cited = []
    cite_ok = None
    for c in kb.citations:
        h = by_chunk.get(c.chunk_id)
        cited.append(f"{c.doc_id} {rr.heading_of(h.content) if h else '?'}")
    if g["kind"] not in rr.NO_ANSWER:
        cite_ok = (not kb.refused) and any(
            (h := by_chunk.get(c.chunk_id)) is not None
            and any(rr.matches(h.doc_id, h.content, rel) for rel in g["relevant"])
            for c in kb.citations
        )
    return Row(id=g["id"], kind=g["kind"], question=g["question"], refused=kb.refused,
               refuse_kind=kb.kind, cited=cited, cite_ok=cite_ok,
               answer=kb.answer, cost_usd=kb.cost_usd)


def summarize(rows: list[Row]) -> dict[str, Any]:
    ans = [r for r in rows if r.kind not in rr.NO_ANSWER]
    no = [r for r in rows if r.kind == "no_hit"]
    near = [r for r in rows if r.kind == "near_miss"]
    cite_acc = round(sum(bool(r.cite_ok) for r in ans) / len(ans), 4) if ans else float("nan")
    s = {
        "citation_acc": cite_acc,
        "false_refuse": [r.id for r in ans if r.refused],
        "no_hit_refused": f"{sum(r.refused for r in no)}/{len(no)}",
        "no_hit_answered": [r.id for r in no if not r.refused],
        "near_miss_refused": f"{sum(r.refused for r in near)}/{len(near)}",
        "near_miss_answered": [r.id for r in near if not r.refused],
        "cost_usd": round(sum(r.cost_usd for r in rows), 4),
    }
    s["pass"] = (
        cite_acc >= TARGET_CITE
        and len(s["false_refuse"]) <= MAX_FALSE_REFUSE
        and not s["no_hit_answered"]
        and sum(r.refused for r in near) >= min(MIN_NEAR_MISS, len(near))
    )
    return s


def print_report(rows: list[Row], s: dict[str, Any]) -> None:
    for r in rows:
        verdict = f"拒·{r.refuse_kind}" if r.refused else "答"
        mark = "✓" if r.ok else "✗"
        print(f"{mark} {r.id} {r.kind:<19} {verdict:<15} {r.question}")
        for c in r.cited:
            print(f"      引用 {c}")
        print(f"      {r.answer[:120].replace(chr(10), ' ')}")
    n_ans = len([r for r in rows if r.kind not in rr.NO_ANSWER])
    print()
    print(f"引用正确率     {s['citation_acc']:.2f}   （有答案 {n_ans} 题，目标 ≥ {TARGET_CITE}）")
    print(f"误拒           {len(s['false_refuse'])}      {' '.join(s['false_refuse'])}（目标 ≤ {MAX_FALSE_REFUSE}）")
    print(f"无答案题拒答   {s['no_hit_refused']}    {' '.join(s['no_hit_answered'])}（目标全部）")
    print(f"擦边题拒答     {s['near_miss_refused']}    {' '.join(s['near_miss_answered'])}（目标 ≥ {MIN_NEAR_MISS}）")
    print(f"成本           ${s['cost_usd']:.4f}")
    print("\n✓ 达标" if s["pass"] else "\n✗ 未达标")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", type=Path)
    ap.add_argument("--only", help="只跑这些题，逗号分隔，如 K13,K19")
    args = ap.parse_args(argv)

    try:
        from chemq.embed import TitanEmbedder  # noqa: PLC0415
        from chemq.kb_answer import answer_from_kb  # noqa: PLC0415
        from chemq.llm import Llm  # noqa: PLC0415

        golden = [json.loads(x) for x in rr.KB_GOLDEN.read_text(encoding="utf-8").splitlines() if x.strip()]
        if args.only:
            keep = set(args.only.split(","))
            golden = [g for g in golden if g["id"] in keep]
        titan = TitanEmbedder()
        vecs = rr.load_embeddings([g["question"] for g in golden], titan.model,
                                  lambda q: titan.embed(q).vector)
        embedder, llm = CachedEmbedder(vecs), Llm()
        rows = []
        for g in golden:
            print(f"  {g['id']} …", end="\r", flush=True)
            rows.append(grade(g, answer_from_kb(g["question"], embedder=embedder, llm=llm)))
    except Exception as e:  # noqa: BLE001
        print(f"✗ 没跑成：{type(e).__name__}: {e}", file=sys.stderr)
        print("  检查：make up / make ingest / aws login --profile chemq / .env 里的 API key", file=sys.stderr)
        return 2

    s = summarize(rows)
    print_report(rows, s)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"summary": s, "rows": [{**r.__dict__, "ok": r.ok} for r in rows]},
            ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if s["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
