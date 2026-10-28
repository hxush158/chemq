# 架构说明

> 状态：截至 W4（2026-10）。标「未实现」的部分在 W5/W6 补。

## 数据流

两种回答引擎并存，**共用同一套工具层、语义层和安全层**。
同一道题可以用 `make eval ENGINE=pipeline|agent` 分别跑，对比两种做法。

```
                              用户问题
                 ┌───────────────┴────────────────┐
                 ▼                                ▼
  ┌─ 做法一：固定流水线 pipeline.py ─┐   ┌─ 做法二：Agent 分析师 agents.py ─┐
  │ Router（Haiku，structured out）  │   │ Agent SDK（Sonnet）              │
  │  → intent: metric_query /        │   │  tools=[]：去掉全部内置工具       │
  │    knowledge_lookup / mixed /    │   │  只挂 3 个进程内工具 mcp__chemq__*│
  │    out_of_scope ──► 拒答         │   │  PreToolUse hook：审计 + 拦截     │
  │                                  │   │  max_turns=8 · max_budget=$0.10  │
  │ metric  → text2sql（Sonnet）     │   │  模型自己决定调哪个工具、调几次   │
  │ knowledge → kb_answer            │   │                                  │
  │ mixed   → 先 SQL，再带 rows 问 KB │   │ 回答出来后：代码 Critic 核数字    │
  └────────────────┬─────────────────┘   └────────────────┬─────────────────┘
                   └───────────────┬──────────────────────┘
                                   ▼
        ┌──────────── 共享工具层 tools.py（唯一实现）────────────┐
        │ list_metrics        指标字典 + 自动生成的语义层目录     │
        │ run_semantic_query  sqlguard → 只读执行，永不抛异常     │
        │ search_knowledge    Titan 向量 + 中文二元 BM25 + RRF    │
        │ 工具说明里写死输出约定（YYYY-MM、两期两行、先求和再除） │
        └──────┬───────────────────────────────┬─────────────────┘
               ▼                               ▼
  ┌─ SQL 侧 ───────────────────┐   ┌─ 知识库侧 kb_answer.py ─────────────┐
  │ sqlguard：AST 校验 · 白名单 │   │ hybrid top 8                        │
  │   · 只许 v_* · 强制 LIMIT  │   │ → 无命中判定（余弦≥0.30/标识符/术语）│
  │ 只读账号 chemq_ro_login    │   │ → Sonnet + search_result 引用        │
  │   + statement_timeout      │   │ → 三个拒答出口：                     │
  │ 语义视图 v_*（口径在这里）  │   │   no_hit / 【资料未覆盖】/ 无引用拦截│
  └────────────────────────────┘   └──────────────────────────────────────┘

  外部 Agent（Claude Code CLI 等）→ mcp_server.py（stdio）→ 同一个 tools.py
```

几点说明：

- **pipeline 里没有 Narrator**。metric_query 直接返回 SQL 结果行，不让模型复述数字；
  mixed 的文字部分由 kb_answer 生成，提示词要求数字原样照抄 rows。
- **Critic 只在 Agent 路径上**，而且是代码（`critic.py`），不是第二次模型调用。
- **qa_audit 留痕未实现**（W6）。目前 Agent 的工具调用记在 `Recorder.audit` 里，只在内存中。

## 三条贯穿全局的设计原则

**1. 数值由 SQL 算，模型不算数**

- pipeline：指标题的答案就是 SQL 结果行；mixed 题按提示词照抄 rows。
- Agent：比率、求和必须写进 SQL；允许做两期相减。回答出来后，代码 Critic
  把每个数字和工具返回的 rows、引用片段、题目逐一比对（容忍四舍五入、万/亿换算、差值）。
- Critic 的边界（实验 B 实测）：末位改动、±3%、**10 倍错误**都能抓到；
  把 A 装置的数字安到 B 装置上（张冠李戴）抓不到 —— 数字本身是"对的"，只是归属错了。
- 评测的分工：eval 核对 SQL 返回的行，Critic 核对写出来的文字。

**2. 口径由语义层定，模型不定口径**

- 模型只能查 `v_*` 视图，查不到 `fact_*` 明细表 —— sqlguard 白名单 + 数据库权限双重保证。
- 折标煤系数等参数从维度表取，禁止硬编码。
- **口径和约定放在共享层，不放在某一个提示词里**：指标字典（`domains/chem/metric_dictionary.yaml`）、
  从数据库自动生成的语义层目录（`domains/chem/semantic_catalog.md`，含维度取值和时间口径）、
  工具说明（`tools.py` 里的 `DESC_*`）。
  W4 的教训：提示词不会跟着走，换一个引擎就丢了；工具说明会跟着工具走到 Agent 和 MCP 客户端。

**3. 纵深防御**

约束只在它所在的那一层生效。下表按"离数据多远"排列：

| 层 | 挡什么 | 现状 | 失效时谁兜底 |
|---|---|---|---|
| Router 意图分类（pipeline） | 超范围（生产成本、利润、人工成本）、注入 | 生效；mixed 题有过度拒答（Q009/Q015/H08） | sqlguard |
| 工具清单（Agent） | 模型用 Bash/Read 绕过工具层 | `tools=[]` + `setting_sources=[]` + PreToolUse hook 拦截非白名单工具 | sqlguard |
| Claude Code 项目权限（外部 Agent） | CLI 读 `.env`、用 psql 直连 | `.claude/settings.json` deny 读 `.env`；**Bash 模式规则不是安全边界**，要禁就整个禁掉 Bash | 数据库权限 |
| 预算 / 轮数上限 | 失控循环、工具太多导致的成本膨胀 | `max_turns=8`、`max_budget_usd=0.10` | — |
| sqlguard | 写操作、越表、无 LIMIT、多语句 | 生效 | 数据库权限 |
| 只读角色 + statement_timeout | 前面几层有 bug | 生效 | — |
| RLS 按大区隔离 | 跨大区看数据 | **目前不生效**：视图以 owner 身份执行绕过 RLS；且未设 scope 时默认返回全量。W5 改成 fail-closed | — |
| 代码 Critic（Agent） | 回答里出现数据里没有的数字 | 生效，边界见原则 1 | 人工 |

实验 C 的结论也写在这里：给 Agent 全套内置工具（27 个）后，简单拒答成本从 $0.003 涨到 $0.016，
一道题 2 次工具调用就撞到预算上限 —— **工具数量本身就是成本**。

已知漏洞（W5）：仓库里的数据库连接串能被 Agent 读到；CLI 在有 Bash 时曾绕过 MCP，
用仓库里的凭据直接 psql 导出 933 行。最后一道防线只能是数据库权限。

## 模型与云服务

| 用途 | 走哪里 | 模型 |
|---|---|---|
| 意图路由 | Anthropic API 直连 | Haiku 4.5 |
| Text-to-SQL、知识库回答、Agent | Anthropic API 直连 | Sonnet 5 |
| 向量化 | AWS Bedrock（ap-southeast-2） | Titan Text Embeddings V2，1024 维 |

- **LLM 走 Anthropic API 直连**：能第一时间用上新特性（structured outputs、
  search_result 引用、Agent SDK）。Agent SDK 支持 `CLAUDE_CODE_USE_BEDROCK=1` 切换，迁移成本是一个环境变量。
- **向量化走 Bedrock Titan**：Anthropic 不提供 embedding；Titan 在同一个 AWS 账号里，
  用 `aws login --profile chemq` 的临时凭据，不放长期密钥。
- 企业客户会问"为什么不全用云厂商的"。答案：先用迭代最快的接口把方案验证出来，
  合规要求明确后再切，切换点已经隔离好。
- 更新的 `claude-sonnet-5-5` / `claude-haiku-5-5` 已发布，换模型留作单独实验（W5），
  要用 eval 跑分对比后再换。

## 数据库引擎抽象

`db.py` 定义 `Engine` Protocol（`readonly_cursor` / `run_query` / `app_cursor`），
按 `settings().sql_dialect` 选实现：

- `PostgresEngine`：一个只读账号，每次连接设 `statement_timeout`、`search_path`，以及数据权限 scope 对应的会话变量（变量名来自领域包，化工是 `chemq.region`）。
- `DorisEngine`：W5 实现，目前是桩。不只是语法翻译 ——
  超时单位不同（`query_timeout` 秒），而且 Doris 读不了会话变量，
  scope 要改成"一个大区一个 role + 一个连接"。
- 语义层目录按方言查找：`domains/chem/<dialect>/semantic_catalog.md`，找不到再用 `domains/chem/semantic_catalog.md`。
- 知识库检索（`hybrid_search`）在 Doris 方案下仍直连 Postgres（pgvector）。

## 评测体系

| 题集 | 数量 | 用途 |
|---|---|---|
| `golden.jsonl` | 20 | 主评测，可以拿来调规则 |
| `holdout.jsonl` | 12（H01–H12） | 留出集，**不许用来调规则**；已被看过，W5 需要换一批新题 |
| `kb_golden.jsonl` | 20（含 4 道擦边题） | 知识库检索与回答 |

- 每次跑 3 遍，按题聚合（模型每次表现不一样，单次结果不可信）。
- 指标：执行正确率、引用率、拒答正确率、成本、p95 延迟；检索另看 Hit@1 / Hit@K / Recall@K。
- `--engine pipeline|agent`、`--set golden|holdout` 记在报告配置里，方便 `agg-vs` 对比。
- 当前执行正确率（W4）：golden 上 pipeline 0.97、Agent 1.00；holdout 上 pipeline 0.75、Agent 0.875。
