# 领域包设计（阶段二 W1 工作文档）

> 2026-10-25 定稿。上游计划见 [`10-flagship-plan.md`](10-flagship-plan.md)。
> 目标：核心引擎领域无关；化工和 NZ 电力各是一个可替换的领域包。

## 一、已定的决定

| # | 决定 |
|---|---|
| 1 | NZ 包用**英文**。语言（locale）是领域包的属性 |
| 2 | 通用规则（时间推算、输出约定、拒答规则）留在引擎的提示词模板里，**中文、英文各一版**；领域包只填空位 |
| 3 | 评测题改成领域包里的**数据文件**（题目 + 参考 SQL），`build_golden.py` 只负责执行 |
| 4 | 权限维度改名 **`scope`**（原 `region`） |
| 5 | `naming.yaml` 拆成两半：引擎要读的进 `domain.yaml`，化工合成数据配置进 `data/seed.yaml` |
| 6 | 知识库存储固定在 Postgres（pgvector），和取数引擎分开 |
| 7 | 分工：第 1–3 步（搬家、模板化、改名）Claude 做、Hui review；第 4–5 步里的新逻辑 Hui 自己写 |

## 二、清单：现在写死的地方

### 领域内容 → `domains/chem/`

| 位置 | 写死了什么 |
|---|---|
| `naming.yaml` | 公司名、装置、产品、客户、能源、合成场景、数据区间 |
| `kb/metric_dictionary.yaml`、`kb/glossary.txt`、`kb/docs/` | 指标字典、术语表、知识库文档 |
| `db/01–04_*.sql`、`db/gen_seed.py` | 化工表结构、视图、角色、RLS、合成数据 |
| `db/build_catalog.py` | `VIEWS` / `DIMS` / `VALUES`；时间口径约定里的 2026-09 示例 |
| `router.py` | `SYSTEM_ROLE` 里的公司名、超范围指标举例、知识题举例；`FEW_SHOT_EXAMPLES` 11 条；`data_range()` 读 naming.yaml |
| `text2sql.py` | 公司名；规则 3（折标煤系数、税率）；规则 14 的维度清单；规则 16 的举例 |
| `schemas.py` | `QueryPlan.dimensions` 的说明（会进 JSON Schema 发给模型） |
| `tools.py` / `mcp_server.py` | `DESC_SEARCH`、服务说明里的业务描述 |
| `agents.py` / `kb_answer.py` | 公司名；引用格式举例 `KB-ENE-2026#7` |
| `config.py` | `allowed_relations`（9 个 `chem.*`） |
| `ai/evals/build_golden.py` | `CASES` / `HOLDOUT_CASES` 写在代码里 |

### 引擎 / 方言 → `dialects.py` 和各 Engine

| 位置 | 问题 |
|---|---|
| `text2sql.py` `DIALECT_PROFILE` | 只有 postgres、doris；缺 duckdb（`strftime`）、snowflake（`to_char`） |
| `tools.py` `DESC_RUN_SQL` | 写死"PostgreSQL"和 `to_char`，没走方言表 |
| `sqlguard.py` | `_qualified()` 默认 schema `chem`；敏感 token 只有两套 |
| `config.py` | `sql_dialect` Literal 只有两种；`dsn` 里有 `if dialect == "doris"` |
| `db.py` | `SET search_path = chem` 写死 |
| `text2sql.py` `schema_context()` | 按 `db/<dialect>/` 找目录 —— 混淆了领域和方言 |
| `build_catalog.py`、`build_golden.py` | 用 `psql` 子进程，换引擎跑不了 |

### 安全

1. DuckDB `read_only=True` 挡不住 `read_csv` / `read_parquet` / `ATTACH` / `INSTALL httpfs`：
   连接时 `enable_external_access=false` 并锁定配置；sqlguard 加 DuckDB 敏感 token。
2. Snowflake 要自己的敏感 token：`COPY INTO`、`GET` / `PUT`、`@stage`、`SYSTEM$`。
3. `rag.hybrid_search` 走 `db.run_query`（当前引擎）—— 切到 DuckDB 会去 DuckDB 找 `kb_chunk`。
4. 权限维度是化工写法：`region` 参数、`chemq.region` 会话变量。

### 运行参数

- `as_of="2026-09-14"` 写死在 pipeline、agents（两处）、cli、api、run_eval、critic_lab
- 数据覆盖区间读 naming.yaml —— NZ 数据持续追加，要从库里查或用评测快照日期
- 评测集路径写死在 `ai/evals/`

### 语言

- 所有提示词"用中文回答"；拒答标记【资料未覆盖】【无法回答】
- router 时间规则、text2sql 16 条约定、目录时间口径 —— 全中文
- `critic.py` 单位和数量级只认中文（万 / 亿 / 吨 / 元）
- `textseg.py` 中文二元切词（RAG 在 MVP 之后，晚点处理）

## 三、目录

```
domains/
  chem/
    domain.yaml              引擎要读的领域配置
    metric_dictionary.yaml
    glossary.txt
    router_examples.yaml     few-shot
    semantic_catalog.md      生成的，不手改
    eval/
      cases.yaml             题目 + 参考 SQL
      golden.jsonl           生成的
      holdout.jsonl          生成的
      kb_golden.jsonl
    kb/docs/*.md
    data/                    化工专用：01–04 SQL、gen_seed.py、seed.yaml
  nz_power/                  W1 只放骨架 + 5 道题
    domain.yaml
    metric_dictionary.yaml   来自数据底座（nz_power_metrics.yaml）
    eval/cases.yaml

ai/src/chemq/
  domain.py                  读 domains/<id>/，提供 Domain 对象
  dialects.py                方言表：月份写法、引擎名、敏感 token
  prompts/zh/*.md            通用规则模板：router、text2sql、agent、kb_answer、时间口径
  prompts/en/*.md            英文版
```

## 四、`domain.yaml` 字段（化工示例）

```yaml
id: chem
locale: zh                      # zh | en
display_name: 示例化工
schema: chem                    # sqlguard 默认 schema、search_path
allowed_relations: [v_plant_daily, v_energy_daily, v_sales_daily, v_monthly_summary,
                    dim_date, dim_plant, dim_product, dim_customer, dim_energy_type]
catalog:
  views: [...]
  dims: [...]
  values: [{title: 装置, sql: "SELECT plant_id, plant_name ..."}]
  date_column: {relation: v_plant_daily, column: date_key}   # 覆盖区间从库里查
prompt_slots:
  filter_dimensions: [装置, 产品, 客户, 大区, 能源]
  out_of_scope_examples: [生产成本, 利润, 人工成本]
  supported_lookalikes: [能源成本, 销量]
  no_hardcode_examples: [折标煤系数, 税率]
  knowledge_example: 综合能耗怎么算
  kb_ref_example: "KB-ENE-2026#7"
scope:
  column: region
  all_value: ALL
eval:
  as_of: "2026-09-14"
  snapshot: null
critic:
  units: [次, 个, 台, 家, 吨, t, "%", 小时, 天, kWh, 元]
```

跟着 `locale` 走、不放进领域包的：拒答标记（zh【资料未覆盖】【无法回答】/ en `[NOT COVERED]` `[CANNOT ANSWER]`）、
数量级词（zh 万 / 亿；en k / m / bn）。

## 五、配置项

| 变量 | 作用 |
|---|---|
| `CHEMQ_DOMAIN=chem` | 选领域包 |
| `CHEMQ_SQL_DIALECT` | 选引擎：postgres / doris / duckdb / snowflake；和领域相互独立 |
| `CHEMQ_DUCKDB_PATH` | 数据底座产出的 `dbt_dev.duckdb` |
| `CHEMQ_KB_DSN` | 知识库（Postgres），和取数引擎分开 |
| `CHEMQ_SCOPE` | 替代 `CHEMQ_REGION`；请求头 `X-Chemq-Region` → `X-Chemq-Scope` |

## 六、评测题文件 `eval/cases.yaml`

```yaml
- id: Q001
  set: golden                  # golden | holdout
  category: answerable         # answerable | refuse | trap
  intent: metric_query
  question: 2026年8月总产量是多少？
  sql: SELECT round(sum(产量_t),1) AS v FROM v_plant_daily WHERE ...
- id: Q006
  set: golden
  category: answerable
  intent: knowledge_lookup
  assert: {type: citation, must_contain: [折标, GB/T 2589, 合格产量]}
```

`build_golden.py`：读 cases.yaml → 用当前引擎执行参考 SQL → 写 golden.jsonl / holdout.jsonl，记录 as_of 和 snapshot。

## 七、W1 步骤（每步化工评测不退步）

| 步 | 内容 | 谁做 | 验收 |
|---|---|---|---|
| 1 | 纯搬家：文件进 `domains/chem/`，路径由 `domain.py` 提供 | Claude，Hui review | `make test` 全过，评测分数不变 |
| 2 | 提示词模板化（中文版） | Claude，Hui review | 快照测试：渲染出的中文提示词和现在一字不差 |
| 3 | `region` → `scope` | Claude，Hui review | `make test` 全过 |
| 4 | `dialects.py` + DuckDBEngine + DuckDB 安全设置 | Hui | `read_csv('/etc/passwd')` 被拦；化工包在 Postgres 上分数不变 |
| 5 | 英文模板 + NZ 包骨架，跑通 5 道题 | Hui | 同一引擎、两个领域包各跑通 5 题 |

第 5 步之前要从数据底座拿到：NZ 的 5 道题、`nz_power_metrics.yaml`、mart 视图清单。
