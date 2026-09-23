"""【参考实现 · W3】混合检索 + 引用。

看之前先回答：
  1. 为什么不能只用向量检索？举一个本项目里必然失败的查询。
  2. RRF 的 k=60 是怎么来的？改成 1 会怎样、改成 1000 会怎样？
  3. 什么情况下应该"检索到了但仍然拒答"？
"""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from chemq.config import settings
from chemq.schemas import Citation

RRF_K = 60

# 决定 1：`折标煤系数依据哪个标准` -> 答案里的关键 token 是 "GB/T 2589"。
# 向量模型会把它压成一个"标准编号"的模糊表示，和 "GB/T 50034" 几乎分不开；
# BM25 则是精确字符串匹配，一击即中。反过来 "能耗怎么算" 这种问法没有共同词，
# BM25 全靠运气，向量才行。两种失败模式互补，所以要 hybrid。

HYBRID_SQL = """
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
  SELECT chunk_id, sum(1.0 / (%(rrf_k)s + r)) AS score
  FROM (SELECT * FROM vec UNION ALL SELECT * FROM bm) u
  GROUP BY chunk_id ORDER BY score DESC LIMIT %(top_k)s
)
SELECT c.chunk_id, c.doc_id, d.title, left(c.content, 500) AS snippet, f.score
FROM fused f
JOIN chem.kb_chunk c    ON c.chunk_id = f.chunk_id
JOIN chem.kb_document d ON d.doc_id   = c.doc_id
ORDER BY f.score DESC
"""

# 决定 2：RRF 的 k 是"名次的钝化程度"。
#   k=1    第 1 名 0.5、第 2 名 0.33 —— 头部权重极大，一路召回说了算，等于没融合
#   k=1000 第 1 名和第 20 名几乎一样 —— 名次信息被抹平，退化成"出现在两路里就行"
#   k=60   源自 RRF 原论文的经验值，在"尊重名次"和"允许另一路翻盘"之间比较平衡
# 这个参数值得你自己在 evals 上扫一遍 [10, 30, 60, 100]，看 Recall@5 怎么动。


def hybrid_search(query: str, query_embedding: list[float], top_k: int = 5) -> list[Citation]:
    with psycopg.connect(settings().pg_dsn_readonly, row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(HYBRID_SQL, {
                "emb": query_embedding, "q": query, "rrf_k": RRF_K, "top_k": top_k * 3,
            })
            rows = cur.fetchall()

    # 决定 3：检索到了但仍要拒答的两种情况 ——
    #   a) 最高分低于阈值：召回的都是弱相关，勉强作答就是在编。
    #   b) top-1 和 top-2 指向互相矛盾的口径版本：这时要把矛盾报出来让人裁决，
    #      而不是挑一个说得更顺的。口径冲突在数据治理项目里极其常见。
    if not rows or rows[0]["score"] < 1 / (RRF_K + 10):
        return []
    return [
        Citation(doc_id=r["doc_id"], title=r["title"],
                 chunk_id=r["chunk_id"], snippet=r["snippet"])
        for r in rows[:top_k]
    ]
