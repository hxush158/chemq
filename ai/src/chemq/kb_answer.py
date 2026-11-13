"""W3 第 6 步：根据资料回答，带引用。设计说明见 docs/03-rag.md 第 10 节。

两个资料来源（NZ W3 第 0 步 ④，问题 2 选 B、问题 3 选 B）：
  知识库   有 KB 的领域才查：Titan 向量化 → 混合检索前 5 条 → 无命中判断（rag.hit_signals），
           判"无"就一条都不给模型
  指标字典 每个领域都有：metric_dictionary.yaml 按条目切成段落，整份给模型，不做检索
           （字典只有几 KB，全给比检索更不会漏）
两边合在一起作为 search_result 块交给 Claude → 回答 + 引用。
两边都没有资料时才直接回答"没找到"，不调 Claude，零成本。

三个出口，都算拒答（refused=True），kind 区分原因：
  no_hit       两个来源都没有资料 —— 字典总在，所以现在只有字典为空时才会出现
  not_covered  Claude 以「【资料未覆盖】」开头 —— 话题相关但资料没回答（擦边题）
  no_citation  Claude 答了但一条引用都没有 —— 很可能在凭记忆答，拦截

为什么用固定开头标记，而不是像 W2 那样给一个 refuse_reason 字段：
structured outputs 和 citations 同时开会直接 400。要引用，就只能用自由文本 + 约定标记。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import yaml

from .config import settings
from .domain import domain
from .prompts import render, string
from .rag import Hit, hit_signals, hybrid_search
from .schemas import Citation

NOT_COVERED = string("marker_not_covered")
NO_HIT_TEXT = string("kb_no_hit")
NO_CITATION_TEXT = string("kb_no_citation")
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


# ---------------- 资料来源：指标字典 ----------------

DICT_DOC_ID = "METRIC-DICT"
_SKIP_KEYS = {"version", "owner"}


def _field_text(key: str, value: Any) -> str:
    if isinstance(value, list):
        value = string("list_sep").join(str(v) for v in value)
    return f"{key}: {value}"


def dictionary_passages() -> list[Hit]:
    """字典 → 段落，形状和知识库检索结果（Hit）一样，后面的组装、引用解析不用分两套。

    不按 chem / NZ 的字段写死：顶层每个键下，
      列表 → 每项一段（标题取 name / view / 序号，字段一行一个 text 块，引用能精确到字段）
      字典 → 每个键一段（NZ 的 conventions）
    chunk_id 从 1 开始连续编号；doc_id 固定为 METRIC-DICT，和知识库文档区分。
    """
    data = yaml.safe_load(domain().metric_dictionary_path.read_text(encoding="utf-8")) or {}
    title = string("dict_title")
    out: list[Hit] = []

    def add(label: str, paras: list[str]) -> None:
        out.append(Hit(
            chunk_id=len(out) + 1, doc_id=DICT_DOC_ID, title=title,
            content=f"{title} › {label}\n" + "\n\n".join(p for p in paras if p.strip()),
            rrf=0.0, cos_sim=None, vec_rank=None, bm_rank=None,
        ))

    for section, value in data.items():
        if section in _SKIP_KEYS:
            continue
        if isinstance(value, dict):
            for key, v in value.items():
                add(f"{section} › {key}", [_field_text(key, v)])
        elif isinstance(value, list):
            for i, item in enumerate(value, start=1):
                if isinstance(item, dict):
                    label = str(item.get("name") or item.get("view") or i)
                    add(f"{section} › {label}", [_field_text(k, v) for k, v in item.items()])
                else:
                    add(f"{section} › {i}", [str(item)])
    return out


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
        blocks.append({"type": "text", "text": string("kb_sql_results", extra={"rows": rows})})
    blocks.append({"type": "text", "text": string("kb_user_question", extra={"question": question})})
    return blocks


# ---------------- 解析回答 ----------------

def parse_answer(content: Sequence[Any], hits: Sequence[Hit]) -> tuple[str, list[Citation]]:
    """拼出回答文本，收集引用（每个 chunk 只留一条，snippet 用第一次被引用的原文）。"""
    texts: list[str] = []
    cites: dict[tuple[str, int], Citation] = {}   # 知识库和字典的 chunk_id 会重号，要连 doc_id 一起区分
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
            cites.setdefault((h.doc_id, h.chunk_id), Citation(
                doc_id=h.doc_id, title=h.title, chunk_id=h.chunk_id,
                snippet=(c.cited_text or "")[:500],
            ))
    return "".join(texts).strip(), list(cites.values())


# ---------------- 主流程 ----------------

def answer_from_knowledge(
    question: str,
    *,
    sql_rows: list[dict] | None = None,
    embedder: Any = None,
    llm: Any = None,
    search: Callable[..., list[Hit]] = hybrid_search,
    use_kb: bool | None = None,
    dictionary: Sequence[Hit] | None = None,
) -> KbResult:
    """use_kb 默认跟领域包的 knowledge_base；dictionary 默认读领域包字典（测试可传 [] 关掉）。"""
    use_kb = domain().has_kb if use_kb is None else use_kb
    kb_hits: list[Hit] = []
    signals: list[str] = []
    if use_kb:
        if embedder is None:
            from .embed import TitanEmbedder  # noqa: PLC0415
            embedder = TitanEmbedder()
        searched = search(question, embedder.embed(question).vector)
        signals = hit_signals(searched, question)
        # 知识库这一路的门禁不变：判无命中，就一条都不给模型
        kb_hits = list(searched) if signals else []
    dict_hits = dictionary_passages() if dictionary is None else list(dictionary)
    hits = kb_hits + dict_hits                  # 顺序决定 search_result_index，parse_answer 按同一顺序找回
    if not hits:
        return KbResult(answer=NO_HIT_TEXT, refused=True, kind="no_hit")

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


# 旧名字：知识库评测（run_kb_answer.py）和外部调用还在用
answer_from_kb = answer_from_knowledge
