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
| W3 | 11/9–11/19（11/13 顺延，原定 11/15） | NZ 领域包 + 50–100 题评估集（含拒答题） | 准确率、拒答率出数，写进 README |
| W4 | 11/20–11/26（11/13 压缩，原定 11/16–11/22） | FastAPI + Streamlit，部署 Azure Container Apps；3 分钟演示视频（含 Snowflake 录屏，12/2 试用到期前）；英文 README；写进 CV | 公网可访问 |
| 之后 | — | RAG（EA 报告）、发电数据、Langfuse、Azure OpenAI provider、LLM-as-judge、多 Agent / LangGraph | — |

W1 剥离 .NET 的具体内容：删除 `gateway/` 目录（留在 git 历史里），
删掉 Makefile 的 `gateway` 目标，改掉 README 里的 .NET 和 CDK 两处。C# 另外单独学。

W1 结果（10/31，提交 `6815fdc`）：验收达成。同一引擎、两个领域包，固定流程全过：

| 领域包 | 引擎 | 做法 | 结果 | 成本 |
|---|---|---|---|---|
| chem | Postgres | pipeline | golden 20/20（intent / exec / refuse / citation 全 1.0） | $0.14 |
| chem | Postgres | agent | golden 20/20 | $0.31 |
| nz_power | DuckDB（服务库） | pipeline | golden 5/5（intent / exec / refuse 全 1.0） | $0.02 |

过程和设计见 `docs/11-domain-pack-design.md`；留给 W3 的事在那份文档第八节的待办里。

### W2 做法（11/1 定稿，11/2 修订）

**11/2 修订**：nz-power-pipeline 已经用 Terraform 建好了 Snowflake（库 `NZ_POWER`，schema `RAW` / `STAGING` / `MART`，
warehouse `WH_XS`，资源监控 `RM_NZ_POWER` 每月 20 credits，角色 `NZ_LOADER` / `NZ_TRANSFORMER` / `NZ_REPORTER`）。
原计划 ChemQ 自己建库、建角色、导数，会和它冲突，改为：**Snowflake 归数据底座管，ChemQ 只读**。

**定下来的事**
- 账号由 nz-power 那边注册（11/2，Azure Australia East），试用到 12/2 左右，到期账号挂起。账号标识只走 `.env`
- ChemQ 用 `NZ_REPORTER`（只读 `NZ_POWER.MART`）。查询用户 `TYPE=SERVICE` + 密钥对，由 nz-power 的 Terraform 创建，
  公钥用 `~/.snowflake/chemq_reporter.pub`（11/2 已生成、已加密）。密钥对是长期凭证，接受的理由：只读 MART；私钥加密放
  `~/.snowflake/`（`.claude/settings.json` 禁读，preflight 拦 `PRIVATE KEY`）；试用结束账号即停
- 3 张表进 MART 由 nz-power 做：周二加载 RAW，周三 dbt 在 Snowflake 上 build，周四对账并交接连接方式。
  周二卡住就改走"从 DuckDB 导入副本"，周三就位
- Snowflake 上对象名是大写；SQL 里标识符不加引号
- W4 公网演示跑 DuckDB；Snowflake 用评测结果 + 录屏展示，**录屏在试用结束前完成**

**步骤（ChemQ 这一侧）**

| 步 | 内容 | 谁做 | 什么时候 | 验收 |
|---|---|---|---|---|
| 1 | `dialects.py` 加 snowflake；sqlguard：三段式全名、Snowflake 敏感写法；领域包的 schema 按方言配置（DuckDB `main`，Snowflake `MART`） | Hui | 周一–周二 | 新越权测试全拦；chem 测试不变 |
| 2 | SnowflakeEngine（密钥对、role 用 `NZ_REPORTER`、query_timeout、拒绝 scope、列名转小写） | Hui；配置项、Makefile、依赖 Claude | 周二–周三 | mock 连接的单元测试 |
| 3 | 连通：reporter 连上、`CREATE TABLE` 被拒；对 `trading_date <= as_of` 核对快照（2,156,450 行、137078471.80） | Hui | 周四（数据就位后） | 两条都过 |
| 4 | Snowflake 版语义层目录（`domains/nz_power/snowflake/`）+ 跨引擎比对脚本（参考 SQL 用 sqlglot duckdb→snowflake 翻译） | Hui | 周四–周五 | 4 道取数题两个引擎结果逐行一致 |
| 5 | NZ 在 Snowflake 上跑评测；文档 | 评测 Hui，文档 Claude | 周五–周六 | 5/5；记录 p95、成本，和 DuckDB 对比 |

W2 验收：同一道题在 DuckDB 和 Snowflake 上结果一致；chem 不退步。

**坑（提前知道的）**
- 两个引擎的 schema 名不同（`main` / `MART`），`domain.yaml` 现在只有一个 `schema`：sqlguard 的白名单、目录生成都依赖它
- sqlguard 的 `_qualified` 只看 schema.表名，三段式 `库.schema.表` 的库名被忽略 —— `OTHER_DB.MART.x` 会被当成 `mart.x` 放行
- `information_schema` 里 schema / 表名是大写，`build_catalog` 按小写查会一行都查不到
- 要拦的写法：`@stage` 引用、`PUT/GET/COPY/LIST/REMOVE/CALL/EXECUTE IMMEDIATE`、`SYSTEM$…`、`RESULT_SCAN`、`INFER_SCHEMA`、`IDENTIFIER('…')`（字符串拼表名，能绕过白名单）、`SNOWFLAKE.ACCOUNT_USAGE`
- Snowflake 上的数据可能比 as_of 新：比对、核对快照都只看 `<= as_of` 的部分。`coverage.yaml` 按主方言（DuckDB）生成，两边共用
- `golden.jsonl` 只能用主方言生成：`make golden DIALECT=snowflake` 会被 Makefile 拒绝，Snowflake 用跨引擎比对
- 每次查询都新建连接要认证，warehouse 从挂起恢复也要几秒：先量 p95 再决定要不要复用连接
- 连接默认 `pyformat` 在客户端拼参数：和 Postgres 一样，没有 params 时不要传 `{}`

### W2 结果（11/7）：验收达成

**步骤和提交**

| 步 | 内容 | 提交 |
|---|---|---|
| 1 | snowflake 方言 + sqlguard（拒三段式全名、AST 拦 `IDENTIFIER()`）；schema 按方言配置（NZ：`main` / `MART`） | `9ce721d` |
| 2–3 | SnowflakeEngine（密钥对、连库前先打开私钥、拒高权限角色、连上后核对角色、超时、列名转小写）；9 条集成测试 `make test-snowflake` | `438f388` `f489cb2` `2f8096b` |
| 4a | Snowflake 版语义层目录（`information_schema` 大小写不敏感；`coverage.yaml` 只由主方言写，其他引擎的区间必须覆盖它） | `267c019` |
| 4b | 跨引擎比对 `make cross-check`：标准答案（DuckDB 生成）和 Snowflake 结果逐行比，不设容差 | `7b8c6f0` |

**验收结果**
- 权限（直连 Snowflake、绕过 sqlguard）：`CHEMQ_REPORTER` / `NZ_REPORTER` 能读 MART 三个对象；读 CORE、RAW 被拒（002003）；`INSERT` / `CREATE TABLE` / `CREATE VIEW` 被拒（003001）。快照一致：2,156,450 行、137078471.80
- 跨引擎比对：N001–N004 四道取数题 SAME，N005（拒答题）跳过；护栏没有误拦翻译后的 SQL
- 端到端评测（pipeline，golden 5 题）：

| 引擎 | 结果 | 成本 | p95 |
|---|---|---|---|
| DuckDB（服务库） | 5/5（intent / exec / refuse 全 1.0） | $0.0219 | 6.9 s |
| Snowflake（`NZ_POWER.MART`） | 5/5（intent / exec / refuse 全 1.0） | $0.0403 | 10.1 s |

  Snowflake 慢约 3 秒：每次查询新建连接 + 密钥认证，warehouse 空闲 60 秒挂起后第一条要等恢复。
  成本高的那部分推测是提示缓存冷启动（Snowflake 版目录第一次进提示词）；DuckDB 第一次跑也是 $0.04 左右。
- chem：提示词快照全过（提示词一个字没变）；sqlguard 的改动有回归测试。W2 没有重跑 chem 评测

**跨引擎比对抓到的真问题**：`mart_daily_price.avg_price` 在 DuckDB 上用 `avg(DECIMAL)`，结果是 DOUBLE，
"均值恰好 x.xx5"时会舍错。真实数据 44,927 行里错了 43 行（全部少 0.01）；在 0–1000 的相邻两分价格对里，旧写法约 4.6% 舍错。
nz-power `a57681a` 改成整数"分"运算、四舍五入远离零（和 Snowflake `ROUND` 一致），两个引擎逐行一致，加了 4 个单元测试。
ChemQ 重建服务库、重算标准答案后零 diff（N001 不在那 43 行里）。

**这周的经验**
1. 动手建基础设施之前，先看对方是不是已经建好了：Snowflake 差点建了两套（我起草的初始化 SQL 和 nz-power 的 Terraform 重复，执行前发现）
2. 换依赖、换版本后先跑最小的连通测试：snowflake-connector-python 5.x 在密码短语错误时只报 `Authentication failed. Unspecified`，请求没到 Snowflake、登录记录里什么都没有，排查花了半天。引擎现在连库前先自己打开私钥
3. 密码短语生成时就存进密码管理器；写进 `.env` 后用读取 `.env` 的方式验证，不要手敲再验证
4. `uv sync` 会让环境完全等于列出的依赖组：只写 `--extra dev --extra db` 会卸掉 agent 等依赖。用 `make install-all`
5. 跨引擎"结果一致"要逐行比、不设容差，才抓得到 0.01 这种差异

### W3 做法（11/8 定稿，11/10 修订，11/13 顺延）

**11/13 顺延**：这周 nz-power 更新了三轮（发电数据、节点的岛和分区、字典 2026-11 → 11.2、判分规则、holdout 交接），第 0 步原定周一、周二做完，到 11/13 还剩 ③②④。按剩余工作量（5–6 天）把 W3 顺延到 11/19、W4 压缩到 11/20–11/26，不砍评测范围。Azure 账号和额度在 W3 期间先确认。

目标：NZ 领域包 + 评测题（含拒答题），准确率和拒答率出数，写进 README。先做 60 道（golden 40 + holdout 20），时间宽裕再扩到 80–100，扩的部分进 golden。

**定下来的事**
- 没有知识库的领域，定义类问题用**指标字典**回答：字典条目当作资料交给模型，回答要引用条目（和知识库问答同一套做法，资料来源换成字典）
- 查出 0 行 = 正常回答"这段时间没有数据"，**不算拒答**。评测新增 `assert_type: no_data`
- holdout 由 Hui 自己写、写之前不看 golden；第 2 步只用 golden 调规则，holdout 到第 3 步只跑一次
- 第 3 步额外用 Agent 跑一次（golden + holdout），和固定流程并排写进 README；Agent 的新问题记待办，不为它延误 W3
- **11/10 修订**（nz-power 加了发电数据、节点的岛和分区，字典升到 2026-11.1）：
  - 题目纳入三类新内容：发电量、按燃料的发电量加权价、按岛 / 分区的跨节点平均价
  - "全国均价"按字典的 `reference_nodes` 约定回答（说明没有全国统一价 + OTA2201 和 BEN2201 的价格），不拒答
  - 字典要求回答必须带的口径说明（如 "unweighted simple average" + 节点数），固定流程原来没有地方说（取数题 `answer` 为空）：
    text2sql 把说明写进 `SqlCandidate.assumptions`，pipeline 放进 `Answer.notes` 随结果返回；评测用 `notes_must_contain` 检查
  - 跨节点平均的算法（字典 2026-11.2 定为一种）：范围内 `mart_current_price` 的行直接求平均，每个节点时段价格计一次；
    不对各节点日均价再平均，也不对多天的日均价再平均。回答要带三样：unweighted 的说明、节点数、日期范围。
    三样各取最可靠的来源：说明来自模型写的 `assumptions`（不许带数字），节点数来自 SQL 多返回的一列，日期范围来自代码里的查询计划（`time_range`，裁剪之后的）
  - 第 0 步 ① 的完整范围（`run_eval`）：citation 误报；输出写领域和方言；按类型判分（`eval.tolerance: typed`）；`must_contain` 支持同义词组、比较前去掉千位分隔符、英文不区分大小写；holdout 可从外部文件读入（`make eval SET=holdout HOLDOUT=…`）
  - holdout（11/11）：Hui 独立写完 20 题，标准答案由 nz-power 从原始文件独立计算、在 Snowflake 上抽查。文件只存在本机，最终测量时交付。类型：数值、no_data 1、refuse 4、explain、explain_value（对应 `sql_and_citation`，需要 ④ 覆盖 `mixed`）
  - 口径送审：golden 写完后用 `make review-golden` 生成送审表，nz-power 只从口径角度审
  - 出题注意：字典里写着答案的例子不出（北岛 2026-04-08 = 162.08）；要验证算法，得出一道有节点时段不全、两种算法结果不同的题（比如 OHG1101 首日 6/24 所在的分区）

**步骤**

| 步 | 内容 | 谁做 | 时间 | 验收 |
|---|---|---|---|---|
| 0 | 清待办：① `run_eval` 的 citation 误报 + 输出写领域和方言；② 0 行时回答"没有数据"（pipeline 判定，`Answer.no_data`，不算拒答；`mixed` 0 行不再查资料）；③ 给用户看的文字按语言切换；④ 定义类问题用指标字典回答（`knowledge_lookup`），没有知识库时 `mixed` 的解释也用字典（11/13 定：资料来源 = 知识库命中 + 整份字典，合在一起交给模型；chem 无命中题改由模型判「未覆盖」，多一次调用；Agent 路径暂未接字典）；⑤ 口径说明随结果返回（`assumptions` → `Answer.notes`，评测 `notes_must_contain`） | Hui；文案 Claude 起草 | 周一–周二 | 测试全过；chem 评测不退步 |
| 1 | 出题：golden 40（Claude 起草，Hui review）+ holdout 20（Hui 写）；参考 SQL Claude 起草 | 见左 | 周二–周三 | `make golden` 成功；`make cross-check` 在 Snowflake 上全部 SAME |
| 2 | golden 基线 → 分析失败 → 只用 golden 调规则（字典问题集中交给 nz-power） | Hui 跑和改，Claude 分析失败 | 周三–周五 | golden：exec ≥ 0.90，refuse = 1.0 |
| 3 | 最终测量：holdout 只跑一次；golden 连跑 3 次取均值；全部题在 Snowflake 上跑一次；Agent 跑一次 | Hui | 周六 | 如实记录 |
| 4 | README（结果表 + 方法）、docs | Claude 写，Hui review | 周六–周日 | README 有结果表 |

看情况再做：英文 Router 的日级时间规则（第 2 步日级题错得多才补）；Critic 正则搬回代码。

**题目分类**（golden / holdout）

| 类别 | golden | holdout | 例子 |
|---|---|---|---|
| 价格：单日、单节点 | 4 | 2 | 某节点某天的日均价、最高价；按节点名称问（Otahuhu） |
| 价格：区间、排名、对比 | 4 | 2 | 9 月均价最高的 3 个节点；8 月和 7 月比 |
| 跨节点平均和全国价 | 3 | 2 | 南岛某天的平均价（要写明 unweighted + 节点数）；"全国电价"（给两个基准节点） |
| 发电量：按燃料、岛、分区 | 5 | 3 | 9 月哪种燃料发电最多；南岛水电 8 月发了多少 MWh |
| 按燃料的发电量加权价 | 4 | 2 | 北岛风电 7 月卖出的平均价（多天要按发电量加权，不能简单平均） |
| 相对时间 | 4 | 2 | 最近 7 天；上个月；"最近 12 个月"（裁剪到 04-01） |
| 口径陷阱 | 5 | 2 | 夏令时 50 / 46 个时段；新节点首日；负价；kWh 和 MWh；电池储能不算可再生 |
| 没有数据 | 3 | 2 | OHG1101 在 6/24 以前；问 2025 年（代码拒答） |
| 拒答 | 5 | 2 | 需求量、按需求加权的价格、预测、无关问题、注入、批量导出 |
| 定义类 | 3 | 1 | interim 价格是什么意思；为什么有的日子 50 个时段；基准节点是哪两个 |

规矩：标准答案由 `make golden` 执行参考 SQL 生成；参考 SQL 再用 `make cross-check` 在 Snowflake 上核对；题目的节点和日期不和 `router_examples.yaml` 重复；问法多样（新西兰用户的英文说法）。

成本估计：整周 3–5 美元（固定流程每题约 $0.005，Agent 每题约 $0.016）。

## 三项接口（W1 内定稿，两边对着做）

### 1. 数据库适配器（沿用 `db.py` 的 Engine 协议）

现有协议：`dialect`、`readonly_cursor(region)`、`run_query(sql, region, params)`、`app_cursor()`。

- **DuckDBEngine**（W1 ✅）：只读连接，读 `make duckdb-serve` 从 nz-power-pipeline 的 `dbt_dev.duckdb` 生成的服务库
  `domains/nz_power/data/serving.duckdb`（只含白名单的 3 个对象，按 as_of 截断，核对快照后才发布）。
  DuckDB 没有行级权限，权限靠服务库里只有这 3 个对象 + sqlguard 白名单。
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
