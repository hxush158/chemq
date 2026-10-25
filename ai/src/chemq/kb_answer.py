"""W3 第 6 步：根据检索到的资料回答，带引用。设计说明见 docs/03-rag.md 第 10 节。

    问题 → Titan 向量化 → 混合检索前 5 条 → 无命中判断（rag.hit_signals）
       ├─ 判"无"：直接回答"没找到"，不调 Claude，零成本
       └─ 判"有"：5 条作为 search_result 块交给 Claude → 回答 + 引用

三个出口，都算拒答（refused=True），kind 区分原因：
  no_hit       检索层判无命中（第 5 步）—— 辞职信这类完全无关的
  not_covered  Claude 以「【资料未覆盖】」开头 —— 话题相关但资料没回答（擦边题）
  no_citation  Claude 答了但一条引用都没有 —— 很可能在凭记忆答，拦截

为什么用固定开头标记，而不是像 W2 那样给一个 refuse_reason 字段：
structured outputs 和 citations 同时开会直接 400。要引用，就只能用自由文本 + 约定标记。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .config import settings
from .prompts import render
from .rag import Hit, hit_signals, hybrid_search
from .schemas import Citation

NOT_COVERED = "【资料未覆盖】"
NO_HIT_TEXT = "知识库里没有找到和这个问题相关的资料。"
NO_CITATION_TEXT = "这次回答没有标出资料出处，可能不是依据知识库作答，已拦截。请换个问法或联系管理员补充资料。"
MAX_ROWS_IN_PROMPT = 50

# ⚠ 规则 4 的举例故意写得很泛。W2 学到的教训：few-shot / 举例里出现评测题原话就是泄题，
#   分数会虚高。K17–K20 擦边题的具体内容（P01 安全检查、扣奖金、4 月单耗、停止发货）不能出现在这里，
#   tests/test_kb_answer.py 有一条测试专门盯着。
SYSTEM_PROMPT = render("kb_answer")     # 正文在 prompts/<locale>/kb_answer.md


@dataclass
class KbResult:
    answer: str
    refused: bool
    kind: str | None = None                       # no_hit / not_covered / no_citation；正常回答为 None
    citations: list[Citation] = field(default_factory=list)
    cost_usd: float = 0.0
    hits: list[Hit] = field(default_factory=list)  # 给评测用：按 chunk_id 找回章节路径
    signals: list[str] = field(default_factory=list)


# ---------------- 组装请求 ----------------

def to_search_results(hits: Sequence[Hit]) -> list[dict[str, Any]]:
    """每条检索结果 -> 一个 search_result 块。

    title 用 chunk 第一行「《标题》 › 章 › 条」；正文按空行拆成多个 text 块 ——
    Claude 是按整块引用的，拆细了引用才精确到段落。
    """
    out = []
    for h in hits:
        head, _, body = h.content.partition("\n")
        paras = [p.strip() for p in body.split("\n\n") if p.strip()] or [head]
        out.append({
            "type": "search_result",
            "source": f"kb://{h.doc_id}#{h.chunk_id}",
            "title": head,
            "content": [{"type": "text", "text": p} for p in paras],
            "citations": {"enabled": True},
        })
    return out


def build_user_content(question: str, hits: Sequence[Hit],
                       sql_rows: list[dict] | None = None) -> list[dict[str, Any]]:
    blocks = to_search_results(hits)
    if sql_rows:
        rows = json.dumps(sql_rows[:MAX_ROWS_IN_PROMPT], ensure_ascii=False, default=str)
        blocks.append({"type": "text", "text": f"数据库查询结果（系统已算好，照抄，不要重算）：\n{rows}"})
    blocks.append({"type": "text", "text": f"用户问题：{question}"})
    return blocks


# ---------------- 解析回答 ----------------

def parse_answer(content: Sequence[Any], hits: Sequence[Hit]) -> tuple[str, list[Citation]]:
    """拼出回答文本，收集引用（每个 chunk 只留一条，snippet 用第一次被引用的原文）。"""
    texts: list[str] = []
    cites: dict[int, Citation] = {}
    for b in content:
        if getattr(b, "type", None) != "text":
            continue
        texts.append(b.text)
        for c in getattr(b, "citations", None) or []:
            if getattr(c, "type", None) != "search_result_location":
                continue
            i = c.search_result_index
            if not 0 <= i < len(hits):
                continue
            h = hits[i]
            cites.setdefault(h.chunk_id, Citation(
                doc_id=h.doc_id, title=h.title, chunk_id=h.chunk_id,
                snippet=(c.cited_text or "")[:500],
            ))
    return "".join(texts).strip(), list(cites.values())


# ---------------- 主流程 ----------------

def answer_from_kb(
    question: str,
    *,
    sql_rows: list[dict] | None = None,
    embedder: Any = None,
    llm: Any = None,
    search: Callable[..., list[Hit]] = hybrid_search,
) -> KbResult:
    if embedder is None:
        from .embed import TitanEmbedder  # noqa: PLC0415
        embedder = TitanEmbedder()
    hits = search(question, embedder.embed(question).vector)
    signals = hit_signals(hits, question)
    if not signals:
        return KbResult(answer=NO_HIT_TEXT, refused=True, kind="no_hit", hits=hits)

    if llm is None:
        from .llm import Llm  # noqa: PLC0415
        llm = Llm()
    res = llm.complete(
        model=settings().model_sql,
        system=SYSTEM_PROMPT,
        user=build_user_content(question, hits, sql_rows),
        max_tokens=1024,
    )
    text, cites = parse_answer(res.content, hits)

    def result(answer: str, refused: bool, kind: str | None = None) -> KbResult:
        # 不用 dict(...) + **展开：Pylance 会把几种值的类型并成一个 union，每个参数都报类型不匹配
        return KbResult(answer=answer, refused=refused, kind=kind, citations=cites,
                        cost_usd=res.usage.cost_usd, hits=hits, signals=signals)

    if NOT_COVERED in text:                     # 不只看开头：标记出现在哪都按未覆盖处理（fail-closed）
        return result(text.replace(NOT_COVERED, "").strip(), True, "not_covered")
    if not cites:
        return result(NO_CITATION_TEXT, True, "no_citation")
    return result(text, False)
