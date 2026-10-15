"""W3：制度/口径文档的混合检索。设计说明见 docs/03-rag.md。

验收标准：
  · Recall@5 ≥ 0.85
  · 每个知识型回答必须带至少 1 条引用，引用正确率 ≥ 90%
  · 无命中时必须说"没查到"，不得凭记忆作答

TODO(W3):
  1. ingest：文档 -> 分块（制度类按条款，其余按标题层级，400~800 token，overlap 80）
            -> Titan 向量化 -> 写 kb_chunk（tsv 用 textseg.doc_lexemes）
  2. ✅ 第 5 步：用评测集标定 rag_min_cosine，加术语字面命中（kb/glossary.txt）
  3. 第 6 步：回答时用 Claude 的 search results content block 传证据，自动产出引用
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from .config import settings
from .db import run_query
from .schemas import Citation
from .textseg import identifiers, to_tsquery_literal

# 两路召回必须**各自先 ORDER BY ... LIMIT**，再融合。
#
# 原来的写法是 `SELECT ..., row_number() OVER (ORDER BY ...) AS r FROM kb_chunk ... LIMIT 20`：
#   · 查询本身没有 ORDER BY，LIMIT 20 取到的不保证是前 20 名
#   · HNSW 索引只在「ORDER BY embedding <=> x LIMIT n」这个形状下才会被用上，
#     窗口函数版本会全表扫描、全表排序
#
# 向量这一路额外带出原始余弦距离 —— RRF 分数是名次，判断"无命中"必须看它。
#
# 两路的条数分开传（n_vec / n_bm）：评测时把某一路设成 0，就得到"只用向量"/"只用 BM25"，
# 三种方式跑的是同一段 SQL，比较才公平。
HYBRID_SQL = """
WITH vec AS (
  SELECT chunk_id, d, row_number() OVER (ORDER BY d) AS r
  FROM (
    SELECT chunk_id, embedding <=> %(emb)s::vector AS d
    FROM chem.kb_chunk
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> %(emb)s::vector
    LIMIT %(n_vec)s
  ) t
),
bm AS (
  SELECT chunk_id, row_number() OVER (ORDER BY s DESC) AS r
  FROM (
    SELECT chunk_id, ts_rank(tsv, %(tsq)s::tsquery) AS s
    FROM chem.kb_chunk
    WHERE tsv @@ %(tsq)s::tsquery
    ORDER BY s DESC
    LIMIT %(n_bm)s
  ) t
),
fused AS (
  SELECT chunk_id, sum(1.0 / (%(rrf_k)s + r))::float8 AS rrf
  FROM (SELECT chunk_id, r FROM vec UNION ALL SELECT chunk_id, r FROM bm) u
  GROUP BY chunk_id
)
SELECT c.chunk_id, c.doc_id, d.title, c.content,
       f.rrf,
       (1 - v.d)::float8 AS cos_sim,
       v.r::int AS vec_rank,
       b.r::int AS bm_rank
FROM fused f
JOIN chem.kb_chunk c    ON c.chunk_id = f.chunk_id
JOIN chem.kb_document d ON d.doc_id   = c.doc_id
LEFT JOIN vec v ON v.chunk_id = f.chunk_id
LEFT JOIN bm  b ON b.chunk_id = f.chunk_id
ORDER BY f.rrf DESC
LIMIT %(top_k)s
"""


# mode -> (用向量这一路, 用 BM25 这一路)
MODES: dict[str, tuple[bool, bool]] = {
    "hybrid": (True, True),
    "vector": (True, False),
    "bm25": (False, True),
}


@dataclass
class Hit:
    """一条检索结果。比 Citation 多带了"它是怎么被找到的"——
    W3 的主题是每一环都要能单独测，所以名次和相似度要一路带到评测里。"""

    chunk_id: int
    doc_id: str
    title: str
    content: str
    rrf: float
    cos_sim: float | None     # 向量这一路的原始余弦相似度；只被 BM25 召回时为 None
    vec_rank: int | None      # 在向量这一路里排第几；没进前 N 为 None
    bm_rank: int | None       # 在 BM25 这一路里排第几；没字面命中为 None

    @property
    def snippet(self) -> str:
        return self.content[:500]

    def to_citation(self) -> Citation:
        return Citation(doc_id=self.doc_id, title=self.title,
                        chunk_id=self.chunk_id, snippet=self.snippet)


def vector_literal(emb: Sequence[float]) -> str:
    """pgvector 的文本格式 '[0.1,0.2,...]'。ingest 写入和检索查询共用。"""
    return "[" + ",".join(f"{x:.7g}" for x in emb) + "]"


def hybrid_search(
    query: str,
    query_embedding: Sequence[float],
    top_k: int | None = None,
    *,
    rrf_k: int | None = None,
    candidates: int | None = None,
    mode: str = "hybrid",
) -> list[Hit]:
    """向量 + BM25 各取前 N，用 RRF 融合后返回前 top_k。

    单用向量会漏掉 "GB/T 2589" 这种标识符，单用 BM25 会漏掉 "能耗怎么算" 这种问法。
    **它只负责找，不负责判断找得对不对** —— 那是 is_no_hit() 的事。

    mode：hybrid（默认）/ vector / bm25。后两个只给评测用，线上永远是 hybrid。
    """
    if mode not in MODES:
        raise ValueError(f"mode 只能是 {sorted(MODES)}，收到 {mode!r}")
    use_vec, use_bm = MODES[mode]
    s = settings()
    n = s.rag_candidates if candidates is None else candidates
    params = {
        "emb": vector_literal(query_embedding),
        "tsq": to_tsquery_literal(query),
        "rrf_k": s.rag_rrf_k if rrf_k is None else rrf_k,
        "n_vec": n if use_vec else 0,
        "n_bm": n if use_bm else 0,
        "top_k": s.rag_top_k if top_k is None else top_k,
    }
    return [Hit(**r) for r in run_query(HYBRID_SQL, params=params)]


GLOSSARY_PATH = Path(__file__).resolve().parents[3] / "kb" / "glossary.txt"


@lru_cache
def load_glossary(path: Path = GLOSSARY_PATH) -> tuple[str, ...]:
    """kb/glossary.txt：一行一个词，# 开头是注释。文件不存在就当空表（术语信号不起作用）。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ()
    return tuple(x.strip() for x in lines if x.strip() and not x.lstrip().startswith("#"))


def hit_signals(
    hits: Sequence[Hit],
    query: str,
    min_cosine: float | None = None,
    glossary: Sequence[str] | None = None,
) -> list[str]:
    """判定"找到了"的依据，返回成立的信号名；空列表 = 无命中。

    **不能看 RRF 分数**：它只反映名次。向量检索永远会返回最近的 N 条，
    哪怕问的是"帮我写辞职信"，第一名照样拿 1/(k+1)。参考实现原来就是用 RRF 分数设阈值，
    结果是无命中拒答永远不会触发。

    看三个真正反映相关度的信号，任一成立就算找到了：
      余弦    向量这一路最高的余弦相似度 ≥ min_cosine
      标识符  问题里有 ASCII 标识符（GB/T、P02……），某条结果正文里字面出现了它
              —— 向量最弱、BM25 最强的场景，不能因为余弦低就判无命中
      术语    问题里有术语表（kb/glossary.txt）里的词，某条结果正文里也有它
              —— 例：「利润怎么算」和"暂不支持的指标"语义不像，但字面上说的就是它
    "BM25 命中了几个中文二元组"本身不算：「怎么」「安全」这种词太容易撞上，
    所以中文只认术语表里的词。
    """
    if not hits:
        return []
    thr = settings().rag_min_cosine if min_cosine is None else min_cosine
    terms = load_glossary() if glossary is None else glossary
    out = []

    best = max((h.cos_sim for h in hits if h.cos_sim is not None), default=None)
    if best is not None and best >= thr:
        out.append("余弦")

    idents = identifiers(query)
    if idents and any(i in h.content.lower() for h in hits for i in idents):
        out.append("标识符")

    q_terms = [t for t in terms if t in query]
    if q_terms and any(t in h.content for h in hits for t in q_terms):
        out.append("术语")

    return out


def is_no_hit(
    hits: Sequence[Hit],
    query: str,
    min_cosine: float | None = None,
    glossary: Sequence[str] | None = None,
) -> bool:
    """"检索到了"不等于"找到了"。判定依据见 hit_signals()。"""
    return not hit_signals(hits, query, min_cosine, glossary)
