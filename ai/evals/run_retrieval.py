#!/usr/bin/env python3
"""检索评测 —— 只评"找没找对"，不调大模型。

    make eval-rag

同一批题用三种方式各查一次：只用向量 / 只用 BM25 / 混合（线上用的）。
检索里没有大模型，同样的输入每次结果都一样 —— 跑一次就够，不用像 W2 那样连跑 3 次看噪声。

指标（只在有标准答案的题上算，即 kind != no_hit）：
  Hit@1     第 1 条就是正确章节。知识库很小时（34 块）Hit@5 几乎总是满分、分不出高低，看这个。
  Hit@K     前 K 条里至少有一条是标注的正确章节。
  Recall@K  标注的几个正确章节，前 K 条里找到了几个（每题算比例，再取平均）。
            需要两条依据才能答的题（K04），只看 Hit 会漏掉"缺了一半"，要看这个。

无命中判断（rag.is_no_hit，按混合检索的前 5 条判）：
  误拒      有答案，却判成"没有"
  误答      无答案题（no_hit：辞职信这类完全无关的），却判成"有"     ← 必须为 0
  擦边题    near_miss：话题沾边、库里其实没答案。检索层本来就分不清"相关"和"有答案"，
            只记录不计分，留给第 6 步由大模型对照原文判断
  另外扫一遍阈值，列出每个阈值下的误拒/误答/擦边判有 —— rag_min_cosine 按数据定，不靠猜。

退出码：0 通过；1 无答案题有误答；2 没跑成（数据库没起、知识库是空的、拿不到 AWS 凭证）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ai" / "src"))

KB_GOLDEN = ROOT / "ai" / "evals" / "kb_golden.jsonl"
CACHE = ROOT / "runs" / "kb_query_emb.json"
MODES = ("vector", "bm25", "hybrid")
MODE_LABEL = {"vector": "向量", "bm25": "BM25", "hybrid": "混合"}
K = 8                                            # 和 config.rag_top_k 一致：评测线上用的条数
NO_ANSWER = ("no_hit", "near_miss")             # 没有标准答案的两类，不算 Hit/Recall
THRESHOLDS = (0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45)


# ---------------- 判对 ----------------

def heading_of(content: str) -> str:
    """chunk 正文第一行是「《标题》 › 章 › 条」，取标题之后那段。"""
    first = content.split("\n", 1)[0]
    return first.split(" › ", 1)[1] if " › " in first else ""


def matches(doc_id: str, content: str, rel: dict[str, str]) -> bool:
    """标注的 section 要和 chunk 路径的**最后一段**对上：
    完全相等（"回款率"），或以它加空格开头（"第三条" 对 "第三条 综合能耗的计算依据"）。
    不用"包含"：那样"第一条"会误中"第十一条"之类。"""
    if doc_id != rel["doc_id"]:
        return False
    last = heading_of(content).split(" › ")[-1]
    return last == rel["section"] or last.startswith(rel["section"] + " ")


@dataclass
class ModeResult:
    rank: int | None              # 第一条正确结果排第几（1 起），前 K 条都没有为 None
    found: int                    # 标注的正确章节里找到了几个
    n_rel: int
    top: list[str] = field(default_factory=list)   # 前 K 条是什么，方便看错在哪

    @property
    def hit(self) -> bool:
        return self.rank is not None

    @property
    def recall(self) -> float:
        return self.found / self.n_rel if self.n_rel else float("nan")


def score(hits: Sequence[Any], relevant: list[dict[str, str]]) -> ModeResult:
    rank = None
    found = set()
    for i, h in enumerate(hits, 1):
        for j, rel in enumerate(relevant):
            if matches(h.doc_id, h.content, rel):
                found.add(j)
                rank = rank or i
    top = [f"{h.doc_id} {heading_of(h.content)}" for h in hits]
    return ModeResult(rank=rank, found=len(found), n_rel=len(relevant), top=top)


# ---------------- 问题向量（带缓存） ----------------

def load_embeddings(questions: list[str], model: str, embed: Callable[[str], list[float]],
                    cache: Path = CACHE) -> dict[str, list[float]]:
    """缓存键是 模型|问题。换模型或改了题目会自动重新算，其余直接读缓存、不调 AWS。"""
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    out, dirty = {}, False
    for q in questions:
        key = f"{model}|{q}"
        if key not in data:
            data[key] = embed(q)
            dirty = True
        out[q] = data[key]
    if dirty:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, cache)
    return out


# ---------------- 主流程 ----------------

@dataclass
class QResult:
    id: str
    kind: str
    question: str
    best_cos: float | None                       # 全库最相似那一块的余弦（向量这一路第 1 名）
    modes: dict[str, ModeResult] = field(default_factory=dict)
    signals: list[str] = field(default_factory=list)          # 当前阈值下"找到了"的依据；空 = 判无命中
    no_hit_at: dict[str, bool] = field(default_factory=dict)  # 阈值 -> 是否判无命中（扫描用）

    @property
    def judged_found(self) -> bool:
        return bool(self.signals)


def _cell(m: ModeResult) -> str:
    if not m.hit:
        return "—"
    return f"{m.rank}" if m.n_rel == 1 else f"{m.rank}({m.found}/{m.n_rel})"


def judge(results: list[QResult], no_hit_of: Callable[[QResult], bool]) -> dict[str, list[str]]:
    """按给定的"是否判无命中"函数，数出误拒 / 误答 / 擦边题判有。"""
    return {
        "false_refuse": [r.id for r in results if r.kind not in NO_ANSWER and no_hit_of(r)],
        "false_answer": [r.id for r in results if r.kind == "no_hit" and not no_hit_of(r)],
        "near_miss_found": [r.id for r in results if r.kind == "near_miss" and not no_hit_of(r)],
    }


def summarize(results: list[QResult]) -> dict[str, Any]:
    scored = [r for r in results if r.kind not in NO_ANSWER]
    kinds = sorted({r.kind for r in scored})

    def agg(rs: list[QResult], mode: str) -> dict[str, float]:
        if not rs:
            return {"hit1": float("nan"), "hit": float("nan"), "recall": float("nan"), "n": 0}
        return {
            "hit1": round(sum(r.modes[mode].rank == 1 for r in rs) / len(rs), 4),
            "hit": round(sum(r.modes[mode].hit for r in rs) / len(rs), 4),
            "recall": round(sum(r.modes[mode].recall for r in rs) / len(rs), 4),
            "n": len(rs),
        }

    return {
        "overall": {m: agg(scored, m) for m in MODES},
        "by_kind": {k: {m: agg([r for r in scored if r.kind == k], m) for m in MODES} for k in kinds},
        "cos_answerable": sorted(r.best_cos for r in scored if r.best_cos is not None),
        "cos_no_hit": sorted(r.best_cos for r in results
                             if r.kind == "no_hit" and r.best_cos is not None),
        "cos_near_miss": sorted(r.best_cos for r in results
                                if r.kind == "near_miss" and r.best_cos is not None),
        "judge": judge(results, lambda r: not r.judged_found),
        "sweep": {t: judge(results, lambda r, t=t: r.no_hit_at.get(t, True))
                  for t in sorted({t for r in results for t in r.no_hit_at})},
    }


def _w(s: str) -> int:
    """终端里中文占两格，用 len() 对齐会歪。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def _l(s: str, w: int) -> str:
    return s + " " * max(0, w - _w(s))


def _r(s: str, w: int) -> str:
    return " " * max(0, w - _w(s)) + s


def _verdict(r: QResult) -> str:
    v = ("有·" + "+".join(r.signals)) if r.signals else "无"
    wrong = (r.kind == "no_hit" and r.judged_found) or (r.kind not in NO_ANSWER and not r.judged_found)
    return v + (" ✗" if wrong else "")


def print_report(results: list[QResult], summary: dict[str, Any]) -> None:
    W0, W1, WC = 6, 20, 10
    LW = 32                                       # 汇总区左边标签列宽
    print(_l("题目", W0) + _l("类型", W1) + "".join(_r(MODE_LABEL[m], WC) for m in MODES)
          + _r("最高相似度", 14) + "   判定")
    for r in results:
        if r.kind in NO_ANSWER:
            cells = _r("（无答案，不计分）", WC * len(MODES))
        else:
            cells = "".join(_r(_cell(r.modes[m]), WC) for m in MODES)
        cos = f"{r.best_cos:.3f}" if r.best_cos is not None else "-"
        print(_l(r.id, W0) + _l(r.kind, W1) + cells + _r(cos, 14) + "   " + _verdict(r))
    topk = summary.get("k", K)
    print(f"\n名次 = 第一条正确结果排第几；— = 前 {topk} 条都不对；(x/y) = 标注了 y 个正确章节、找到 x 个")
    print("判定 = 当前阈值下 is_no_hit 的结论及依据；✗ = 判错")

    o = summary["overall"]
    print("\n" + _l("", LW) + "".join(_r(MODE_LABEL[m], WC) for m in MODES))
    print(_l(f"Hit@1（{o['hybrid']['n']} 题）", LW) + "".join(_r(f"{o[m]['hit1']:.2f}", WC) for m in MODES))
    print(_l(f"Hit@{topk}", LW) + "".join(_r(f"{o[m]['hit']:.2f}", WC) for m in MODES))
    print(_l(f"Recall@{topk}", LW) + "".join(_r(f"{o[m]['recall']:.2f}", WC) for m in MODES))
    for k, row in summary["by_kind"].items():
        label = f"  Hit@1 {k}（{row['hybrid']['n']}）"      # 按类型看 Hit@1：Hit@5 全是满分
        print(_l(label, LW) + "".join(_r(f"{row[m]['hit1']:.2f}", WC) for m in MODES))

    ca, cn = summary["cos_answerable"], summary["cos_no_hit"]
    print("\n最高相似度")
    print("  有答案题：", " ".join(f"{x:.3f}" for x in ca))
    print("  无答案题：", " ".join(f"{x:.3f}" for x in cn))
    print("  擦边题：  ", " ".join(f"{x:.3f}" for x in summary["cos_near_miss"]))
    if ca and cn:
        gap = min(ca) - max(cn)
        word = "有空隙" if gap > 0 else "有重叠 —— 单靠余弦分不开，要靠标识符/术语信号"
        print(f"  有答案最低 {min(ca):.3f}，无答案最高 {max(cn):.3f}，差 {gap:+.3f}：{word}")

    j = summary["judge"]
    n_ans = len([r for r in results if r.kind not in NO_ANSWER])
    n_no = len([r for r in results if r.kind == "no_hit"])
    n_near = len([r for r in results if r.kind == "near_miss"])
    thr = summary.get("threshold")
    print(f"\n无命中判断（当前阈值 {thr:.2f}）" if thr is not None else "\n无命中判断")
    print(f"  误拒        {len(j['false_refuse'])}/{n_ans}  {' '.join(j['false_refuse'])}")
    print(f"  误答        {len(j['false_answer'])}/{n_no}  {' '.join(j['false_answer'])}"
          + ("" if not j["false_answer"] else "   ← 必须为 0"))
    print(f"  擦边题判有  {len(j['near_miss_found'])}/{n_near}  {' '.join(j['near_miss_found'])}"
          "   （不计分，留给第 6 步）")

    if summary["sweep"]:
        print("\n阈值扫描（三个信号一起用，只变余弦阈值）")
        print("  " + _r("阈值", 6) + _r("误拒", 8) + _r("误答", 8) + _r("擦边判有", 12))
        for t, row in summary["sweep"].items():
            mark = "   ← 当前" if thr is not None and f"{thr:.2f}" == t else ""
            print("  " + _r(t, 6) + _r(str(len(row["false_refuse"])), 8)
                  + _r(str(len(row["false_answer"])), 8) + _r(str(len(row["near_miss_found"])), 12) + mark)

    # 混合不应比两路中较好的那一路差
    best_single = max(o["vector"]["hit"], o["bm25"]["hit"])
    if o["hybrid"]["hit"] < best_single:
        print(f"\n! 混合 Hit@{topk} {o['hybrid']['hit']:.2f} 低于单路最好的 {best_single:.2f}：RRF 融合有问题，先查这个")


def run(k: int = K) -> tuple[list[QResult], dict[str, Any]]:
    from chemq.db import run_query  # noqa: PLC0415
    from chemq.embed import TitanEmbedder  # noqa: PLC0415
    from chemq.config import settings  # noqa: PLC0415
    from chemq.rag import hit_signals, hybrid_search, is_no_hit  # noqa: PLC0415

    golden = [json.loads(x) for x in KB_GOLDEN.read_text(encoding="utf-8").splitlines() if x.strip()]

    n_chunks = run_query("SELECT count(*) AS n FROM chem.kb_chunk")[0]["n"]
    if not n_chunks:
        raise RuntimeError("知识库是空的。先跑 make ingest（make seed 之后都要重跑）")

    embedder = TitanEmbedder()
    embs = load_embeddings([g["question"] for g in golden], embedder.model,
                           lambda q: embedder.embed(q).vector)

    results = []
    for g in golden:
        emb = embs[g["question"]]
        r = QResult(id=g["id"], kind=g["kind"], question=g["question"], best_cos=None)
        for mode in MODES:
            hits = hybrid_search(g["question"], emb, top_k=k, mode=mode)
            r.modes[mode] = score(hits, g["relevant"])
            if mode == "vector" and hits:
                r.best_cos = hits[0].cos_sim
            if mode == "hybrid":                           # 线上用的就是混合，按它判无命中
                r.signals = hit_signals(hits, g["question"])
                r.no_hit_at = {f"{t:.2f}": is_no_hit(hits, g["question"], min_cosine=t)
                               for t in THRESHOLDS}
        results.append(r)
    summary = summarize(results)
    summary["threshold"] = settings().rag_min_cosine
    summary["k"] = k
    return results, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--k", type=int, default=K)
    ap.add_argument("--json", type=Path, help="把逐题结果和汇总写到这个文件")
    args = ap.parse_args(argv)

    try:
        results, summary = run(args.k)
    except Exception as e:  # noqa: BLE001 —— 没跑成的原因很多，统一给提示、退出码 2
        print(f"✗ 检索评测没跑成：{type(e).__name__}: {e}", file=sys.stderr)
        print("  检查：make up（数据库）/ make ingest（知识库）/ aws login --profile chemq（凭证）",
              file=sys.stderr)
        return 2

    print_report(results, summary)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "k": args.k,
            "summary": summary,
            "questions": [
                {**{x: v for x, v in asdict(r).items() if x != "modes"},
                 "modes": {m: {**asdict(mr), "hit": mr.hit} for m, mr in r.modes.items()}}
                for r in results
            ],
        }
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✓ 明细写到 {args.json}")
    if summary["judge"]["false_answer"]:
        print("\n✗ 无答案题被判成有答案：系统会拿不相关的资料硬答。退出码 1", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
