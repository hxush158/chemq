"""W3 交付物：制度/口径文档的混合检索。

验收标准：
  · Recall@5 ≥ 0.85（用 evals/golden.jsonl 里 knowledge_lookup 类问题）
  · 每个知识型回答必须带至少 1 条引用，引用正确率 ≥ 90%
  · 无命中时必须说"没查到"，不得凭记忆作答

TODO(W3):
  1. ingest：PDF/Docx -> 分块（按标题层级，chunk 400~800 token，overlap 80）
  2. embed：选型见 docs/03-rag.md；写入 kb_chunk.embedding
  3. hybrid_search：向量 cosine + BM25(tsv) 各取 top-20，用 RRF 融合后取 top-5
  4. 回答时用 Claude 的 search results content block 传证据，自动产出引用
"""
from __future__ import annotations

from .db import run_query
from .schemas import Citation

RRF_K = 60


def hybrid_search(query: str, query_embedding: list[float], top_k: int = 5) -> list[Citation]:
    """Reciprocal Rank Fusion：向量召回补语义，BM25 召回补专有名词（装置代号、GB 标准号）。
    单用向量会漏掉 "GB/T 2589" 这种字符串，单用 BM25 会漏掉 "能耗怎么算" 这种问法。
    """
    sql = """
    WITH vec AS (
      SELECT chunk_id, row_number() OVER (ORDER BY embedding <=> %(emb)s::vector) AS r
      FROM chem.kb_chunk WHERE embedding IS NOT NULL LIMIT 20
    ),
    bm AS (
      SELECT chunk_id, row_number() OVER (
               ORDER BY ts_rank(tsv, plainto_tsquery('simple', %(q)s)) DESC) AS r
      FROM chem.kb_chunk WHERE tsv @@ plainto_tsquery('simple', %(q)s) LIMIT 20
    ),
    fused AS (
      SELECT chunk_id, sum(1.0 / (%(k)s + r)) AS score
      FROM (SELECT * FROM vec UNION ALL SELECT * FROM bm) u
      GROUP BY chunk_id ORDER BY score DESC LIMIT %(k2)s
    )
    SELECT c.chunk_id, c.doc_id, d.title, left(c.content, 400) AS snippet
    FROM fused f JOIN chem.kb_chunk c ON c.chunk_id = f.chunk_id
    JOIN chem.kb_document d ON d.doc_id = c.doc_id
    ORDER BY f.score DESC
    """
    rows = run_query(sql)  # TODO(W3): 换成参数化执行（护栏对固定 SQL 不适用）
    return [Citation(**r) for r in rows][:top_k]
