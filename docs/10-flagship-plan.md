# 阶段二：旗舰项目引擎层（2026-10-26 起）

> 来源：两份交接（求职方向讨论、nz-power-pipeline 会话），2026-10-22。
> 总计划在 Docs 文档「EMI 项目 14 周计划」的「方向调整」一节；本文只写 ChemQ 这一侧。

## 为什么改方向

- 求职主攻 **AI Engineer + FDE**（Forward Deployed Engineer）。带 AI 成分的 Integration / Solution 岗是外延。
- 不新开项目：**ChemQ 升级为引擎层，nz-power-pipeline 作为数据底座**，合成一个旗舰展示 ——
  用自然语言查询和分析 NZ 电力市场数据的 Agent。
- 叙事：同一个引擎先服务中国化工、再适配 NZ 电力，这就是 FDE 的工作方式。
- 两个项目、两个会话分开推进：本仓库做引擎，nz-power-pipeline 做数据底座。
- 每周投入 10–12 小时。

## 起点：ChemQ 已经有的

原 6 周计划的 W1–W4 已完成，可以直接写进 README，不用重做：

- 两种引擎：固定流水线（Router → text2sql → sqlguard → 只读执行）和 Agent SDK 分析师
- RAG：Titan 向量 + 中文 BM25 + RRF，search_result 引用，三个拒答出口
- MCP server（同一套工具层给外部 Agent 用）
- 代码 Critic（核对回答里的数字）
- 评测：golden 20 / holdout 12 / kb_golden 20，每题跑 3 遍按题汇总
- 数据库引擎抽象：`db.py` 的 `Engine` Protocol，`sql_dialect` 切换，Doris 桩

原 W5（AWS 部署 + .NET 网关）和 W6（评测体系与报告）被本计划替代，
其中部署改到阶段二 W4，评测方法沿用到 NZ 评估集。

## 排期（ChemQ 这一侧）

| 周 | 日期 | 做什么 | 验收 |
|---|---|---|---|
| W1 | 10/26–11/1 | 领域包抽出；数据库适配器扩展（DuckDB）；剥离 .NET | 同一引擎用化工包和 NZ 电力包各跑通 5 题；化工评测不退步 |
| W2 | 11/2–11/8 | Snowflake 适配器（账号 W2 才开，之前对着 DuckDB 做） | 同一道题在 DuckDB 和 Snowflake 上结果一致 |
| W3 | 11/9–11/15 | NZ 领域包 + 50–100 题评估集（含拒答题） | 准确率、拒答率出数，写进 README |
| W4 | 11/16–11/22 | FastAPI + Streamlit，部署 Azure Container Apps；3 分钟演示视频；英文 README；写进 CV | 公网可访问 |
| 之后 | — | RAG（EA 报告）、发电数据、Langfuse、Azure OpenAI provider、LLM-as-judge、多 Agent / LangGraph | — |

W1 剥离 .NET 的具体内容：删除 `gateway/` 目录（留在 git 历史里），
删掉 Makefile 的 `gateway` 目标，改掉 README 里的 .NET 和 CDK 两处。C# 另外单独学。

## 三项接口（W1 内定稿，两边对着做）

### 1. 数据库适配器（沿用 `db.py` 的 Engine 协议）

现有协议：`dialect`、`readonly_cursor(region)`、`run_query(sql, region, params)`、`app_cursor()`。

- **DuckDBEngine**（W1）：只读连接，读 nz-power-pipeline 产出的 `dbt_dev.duckdb`（路径走配置）。
  DuckDB 没有行级权限，权限靠只暴露 mart 视图。
- **SnowflakeEngine**（W2）：用只读角色（reporter）连接。行级权限和 Doris 同类，
  靠"选哪个连接 / 角色"表达，不靠会话变量。
- sqlglot 的 dialect 由 `Engine.dialect` 决定，业务代码里不出现 `if dialect == ...`。
- 待定：NZ 数据的权限维度用什么（`dim_node` 的 region / island 目前为空，W2 补）。

### 2. 语义层文件格式

- 沿用 `kb/metric_dictionary.yaml` 的结构：name、en、unit、view、column、definition、pitfalls。
- 数据底座侧产出 NZ 版（暂名 `nz_power_metrics.yaml`），由 dbt `_marts.yml` 的字段描述加手写指标定义组成。
  模型只能查 `mart_*` 视图，不能直接查 `fact_*`。
- NZ 数据已知口径，要写进 definition / pitfalls：
  - 交易时段每天 48 个，夏令时切换日为 46 或 50
  - 价格单位 NZD/MWh，时间为 NZ 本地时间
  - 同一时段可能有 interim 和 final 两个版本，分析默认取 final
  - 价格数据里没有电量，"平均价"是节点简单平均，不是电量加权 —— 必须写进 pitfalls
- 可用视图：`mart_current_price`、`mart_daily_price`、`v_dim_node`（W2 之后加发电相关）。

### 3. 领域包目录结构

每个领域一个目录，引擎通过配置选择领域，核心代码里不写死业务名称：

- `domain.yaml`（原 `naming.yaml` 的角色：实体与命名）
- `metric_dictionary.yaml`
- `glossary.txt`
- `semantic_catalog.md`（由 `build_catalog.py` 生成）
- `golden.jsonl` / `holdout.jsonl`（评估集）
- `kb/docs`（RAG 文档，MVP 之后）
- 数据加载脚本（化工是 `gen_seed.py`；NZ 由数据底座侧提供）

### 评估集规则（NZ 版）

- 沿用 `build_golden.py` 的做法：标准答案由执行参考 SQL 生成，不手填。
- NZ 数据会持续追加和修订，所以评估集固定一个 as_of 日期和数据快照，换快照才重算答案。
- 每题标注类别：可回答 / 应拒答 / 口径陷阱，拒答率单独统计。
- golden 和 holdout 分开，holdout 不拿来调规则（W4 的教训）。

## 几条约束

- **公开仓库里不出现真实客户名**。化工那段经历在 README 里写成"一家中国化工企业"；
  引用的成果数字要先确认能对外说。
- **在线演示不依赖 Snowflake 试用期**（30 天，W2 开始算，W4 之后不久就到期）。
  演示站用 DuckDB 文件；Snowflake 用来证明适配器能接、录演示视频。三引擎可插拔正好派上用场。
- **分工不变**：router / text2sql / rag / agents 由 Hui 自己写，Claude 只做样板、review、出题（见 `CLAUDE.md`）。
- 不做：模型训练和微调、复杂前端、多云部署。

## 本周先做

1. 把 `CLAUDE.md` 的进度行改成 W4 完成。
2. 读 `db.py`、`text2sql.py`（以及 router、kb_answer、build_catalog、评测脚本）里所有写死化工语义的地方，
   列出"领域包要抽的清单"，再设计目录和配置。
3. 三项接口的格式在 W1 内和数据底座侧确认。
