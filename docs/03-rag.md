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

## 6. 知识文档与检索评测集

知识文档在 `kb/docs/*.md`，文件开头的 front matter 对应 `kb_document` 的字段：

| doc_id | 文档 | 类型 | 切块方式 |
|---|---|---|---|
| KB-ENE-2026 | 能耗考核管理办法（2026 版） | 制度 | 按条款（`###`） |
| KB-OPS-STOP | 生产装置停车分类与统计规定 | 制度 | 按条款（`###`） |
| KB-MET-2026 | 经营指标口径说明 | 口径字典 | 按指标（`##`） |
| KB-RPT-202603 | 2026 年 3 月经营分析月报 | 月度经营分析 | 按章节（`##`） |

条款本身就是语义单元，很多只有一两百 token，比 400–800 的建议值小 —— 对制度类文档这是对的，
不要为了凑长度把两个条款拼在一起。每个 chunk 写入时要在正文前加上「《文档标题》› 章 › 条」，
否则单独看"检修期间合格产量为零……"这一段，模型不知道说的是哪份制度。

检索评测集 `ai/evals/kb_golden.jsonl`，每题标注应该命中的 `(doc_id, section)`：

- `identifier`（4 题）：含 GB/T 2589、0.1229、P05、P02 —— 测 BM25 那一路
- `fuzzy`（8 题）：和原文几乎没有共同词，如"氨厂开工不足" —— 测向量那一路
- `refuse_with_source`（1 题）："利润怎么算" —— 库里有依据，依据就是"不支持"
- `no_hit`（3 题）：库里没有 —— 测 `is_no_hit()`，也用来标定 `rag_min_cosine`

Recall@5 只在前三类上算；`no_hit` 单独统计误答率。

## 7. 入库（ingest）

```
make install-rag          # 数据库 + boto3[crt]（>=1.41 才读得了 aws login 的凭证）
aws login --profile chemq # 临时凭证，最长 12 小时
make ingest-dry           # 只切块看结果
make ingest               # 切块 -> Titan 向量化 -> 写库
```

两条设计：

- **先全部向量化，再一次事务写库。** 向量化是几十次网络调用，中途凭证过期或断网，
  库里不会出现"一半新一半旧"。
- **`kb/docs` 是唯一来源。** 每次整体同步：先删后插，文件夹里没有的文档也从库里删掉，
  重跑结果一样。所以 `chemq_app` 对两张 kb 表多给了 DELETE（业务表不给）。

`make seed` 会重建 schema、清空知识库，seed 之后要重跑 `make ingest`。

首次入库：4 篇 34 块，3981 tokens，约 6 秒。

## 8. 检索评测（只评"找没找对"）

```
make eval-rag      # 需先 make ingest；问题向量缓存在 runs/kb_query_emb.json，重跑不调 AWS
```

同一批题、同一段 SQL，三种方式各查一次：只用向量 / 只用 BM25 / 混合。
关掉某一路的办法是把那一路的 `LIMIT` 设成 0（`hybrid_search(..., mode=...)`），
所以比较的只是"用不用这一路"，没有别的变量。

- **Hit@5**：前 5 条里至少有一条是正确章节。生成答案只需要一条对的依据，这个最重要。
- **Recall@5**：标注的几个正确章节，前 5 条找到了几个。
- 无答案题不计分，只记"全库最相似那一块"的余弦，和有答案题的放在一起看有没有空隙 —— 第 5 步用。

检索里没有大模型，同样的输入结果每次一样，跑一次就够（W2 的 SQL 评测要连跑 3 次看噪声）。

跑之前的预期：标识符题 BM25 好、向量差；模糊问法反过来；混合不比两者中较好的那个差。
