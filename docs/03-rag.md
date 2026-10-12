# RAG 设计说明（W3）

> 这个文件之前被 `db/01_schema.sql` 和 `rag.py` 引用了两次，但一直没写。

## 1. Embedding 选型：Amazon Bedrock Titan Text Embeddings V2

| | 结论 |
|---|---|
| 模型 | `amazon.titan-embed-text-v2:0`，输出 1024 维，和 `kb_chunk.embedding vector(1024)` 一致 |
| 地区 | `ap-southeast-2`（Sydney），新流程下 project 按联系地址自动分到这里 |
| 输入上限 | 8,192 token / 50,000 字符，chunk 400–800 token 远低于上限 |
| 中文 | 支持，但官方说明是"针对英文优化" —— **所以 BM25 这一路对中文更重要** |
| 凭证 | `aws login --profile chemq`：浏览器登录，临时凭证每 15 分钟自动轮换，不存长期 key |

为什么选它而不是 Voyage / bge-m3：和 W5 上云、求职需要的 AWS 经验最契合。
备选（以后可以做 A/B）：

- **bge-m3**（本地，MIT，1024 维）：中文最好；国内化工企业数据不出内网时最可能的生产方案
- **Voyage voyage-4**：Anthropic 推荐的 embedding 服务商，2 亿 token 免费额度

换模型只要维度仍是 1024，schema 不用动；维度不同就要改 `vector(N)` 并重建索引。

## 2. 中文切词：字二元组（bigram）

PostgreSQL 的 `to_tsvector('simple', ...)` 按空格和标点切词，中文没有空格，
一整句会变成一个词项 —— BM25 对中文等于没开。

做法（`chemq/textseg.py`）：

- 中文按字二元组切：`综合能耗` → `综合` `合能` `能耗`
- ASCII 标识符整体保留并拆出各部分：`GB/T 2589-2020` → `gb/t` `gb` `2589-2020` `2589` `2020`
- 写入：`tsv = array_to_tsvector(%(lexemes)s::text[])` —— 按原样当词项，不再经过解析器
- 查询：`%(tsq)s::tsquery`，词项之间用 OR —— 问句里的「到底」「怎么」文档里没有，AND 会全部落空

已知代价：bigram 有跨词边界的噪声（「合能」），`ts_rank` 没有 IDF，不是真正的 BM25。
语料只有几份文档时可以接受，由向量那一路互补。

## 3. 检索 SQL

两路**各自先 `ORDER BY ... LIMIT N`**，再用 RRF 融合：`score = Σ 1/(k + rank)`。

- 只有 `ORDER BY embedding <=> x LIMIT n` 这个形状才会用上 HNSW 索引
- 向量这一路同时带出原始余弦相似度，供"无命中"判断用

参数在 `config.py`：`rag_rrf_k=60`、`rag_candidates=20`、`rag_top_k=5`。

## 4. 无命中判断：不能看 RRF 分数

RRF 分数是名次，不是相关度。向量检索永远返回最近的 N 条，第一名永远是 1/(k+1)。

`rag.is_no_hit()` 看两个信号，任一成立就算找到了：

1. 向量那一路最高余弦相似度 ≥ `rag_min_cosine`
2. 问题里的 ASCII 标识符（GB/T、P02）在某条结果正文里字面出现

只靠 BM25 撞上几个中文二元组（「成本」「怎么」）不算。

⚠ `rag_min_cosine = 0.35` 是**占位值**，第 5 步用评测集标定：
把「该命中」和「不该命中」的问题各跑一遍，看两组余弦分布在哪里分开。

## 5. 权限

检索走只读账号 `chemq_ro`，需要 `GRANT SELECT ON kb_document, kb_chunk TO chemq_ro`
（在 `db/03_semantic_views.sql`）。写入（ingest）走 `chemq_app`。
