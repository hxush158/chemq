# 环境搭建 · macOS + VS Code

从零到 `make eval WEEK=1` 能跑起来，大约 30 分钟，其中 20 分钟在等下载。

**前置依赖只有两个**：Docker Desktop 和 uv。
本机**不需要**装 PostgreSQL 客户端（psql 走容器），也**不需要**动系统 Python（uv 自带）。

---

## 第 1 步 · 装工具（一次性）

打开「终端」(Terminal.app)，逐条执行。

### 1.1 Xcode 命令行工具 —— 提供 `make` 和 `git`

```bash
xcode-select --install
```

弹窗点「安装」。已经装过会提示 `already installed`，正常。

### 1.2 Docker Desktop

去 <https://www.docker.com/products/docker-desktop/> 下载 **Apple Silicon** 版（M 系列芯片）
或 Intel 版，装完**打开它**，等菜单栏的鲸鱼图标不再转。

验证：

```bash
docker info | head -3      # 有输出就是好的；报 "Cannot connect" 说明 Docker Desktop 没开
```

> Docker Desktop 必须一直开着，数据库跑在里面。关掉它 `make up` 就连不上。

### 1.3 uv —— Python 包管理器

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

装完**关掉终端重开**（让 PATH 生效），然后：

```bash
uv --version
```

### 1.4 VS Code

<https://code.visualstudio.com/> 下载安装。然后装 `code` 命令：
打开 VS Code → `Cmd+Shift+P` → 输入 `shell command` → 选
**Shell Command: Install 'code' command in PATH**。

---

## 第 2 步 · 解压项目并打开

```bash
mkdir -p ~/projects && cd ~/projects
tar xzf ~/Downloads/chemq-scaffold.tar.gz -C . --one-top-level=chemq
cd chemq
code .
```

VS Code 右下角会弹「此工作区有推荐扩展」→ 点 **Install**。
（没弹的话：`Cmd+Shift+X` → 搜索框输入 `@recommended` → 全装。）

---

## 第 3 步 · 配置 API key

```bash
cp .env.example .env
```

在 VS Code 里打开 `.env`，把这行填上你的 key：

```
CHEMQ_ANTHROPIC_API_KEY=sk-ant-api03-粘你的真key到这里

```

Key 在 <https://platform.claude.com/> → API Keys 里建。
`.env` 已经在 `.gitignore` 里，不会被提交。

---

## 第 4 步 · `make up` —— 起数据库

在 VS Code 里按 **`Ctrl+反引号`** 打开集成终端（就在项目目录下），然后：

```bash
make up
```

**它具体做了什么**：

1. 读 `docker-compose.yml`，拉 `pgvector/pgvector:pg16` 镜像（首次约 400MB，要等几分钟）
2. 起一个叫 `chemq-db` 的容器，把容器的 5432 端口映射到你本机的 5432
3. **首次启动**时，PostgreSQL 官方镜像会自动执行挂载进
   `/docker-entrypoint-initdb.d/` 的所有 `.sql` 文件——也就是 `db/` 目录下的
   `01_schema.sql` → `02_seed.sql` → `03_semantic_views.sql` → `04_roles.sql`，
   按文件名顺序。所以建表、灌 12 个月数据、建语义层视图、建只读账号，
   这一条命令全做完了
4. `--wait` 让它等健康检查通过才返回

数据存在 Docker 的 volume 里，`make down` 不会丢，`make reset` 才会删。

**验证**：

```bash
make doctor
```

期望看到：

```
  ✓ docker  Docker version ...
  ✓ docker 守护进程已在运行
  ✓ uv      uv ...
  ✓ make    GNU Make ...
  ✓ .env    已创建
  ✓ 可连接，chem schema 有 18 张表/视图
```

**起不来怎么办**：

| 症状 | 原因 | 怎么办 |
|---|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop 没开 | 打开它，等鲸鱼图标停转 |
| `port is already allocated` | 本机 5432 被别的 PostgreSQL 占了 | `make down`，或改 `docker-compose.yml` 里的 `"5433:5432"`，同时改 `.env` 的端口 |
| `make: command not found` | 没装命令行工具 | `xcode-select --install` |
| 卡在拉镜像 | 网络慢 | `make logs` 另开一个终端看进度 |

---

## 第 5 步 · `make seed` —— 什么时候需要它

**首次 `make up` 之后不用跑 `seed`**，数据已经在了。

`make seed` 是给这两种情况用的：

- 你改了 `naming.yaml`（换成曙光实际的装置/产品命名）
- 你手工改坏了数据，想恢复到干净状态

**它具体做了什么**：

```
1. uv run python db/gen_seed.py > db/02_seed.sql
      读 naming.yaml，按固定随机种子生成 12 个月合成数据的 INSERT 语句
      随机种子固定 = 每次生成的数据完全一样 = 评测集的标准答案才稳定

2. docker compose exec -T db psql ... -f 01 -f 02 -f 03 -f 04
      在容器里重新执行四个 SQL 文件
      01_schema.sql 开头有 DROP SCHEMA chem CASCADE，所以是干净重建

3. uv run python ai/evals/build_golden.py
      用参考 SQL 实跑一遍数据库，把结果写进 ai/evals/golden.jsonl
      这样"评测集的标准答案"永远和"数据"同步 —— 数据变了标准答案自动跟着变
```

第 3 步是关键：**标准答案不是手写的，是从数据库里跑出来的。**
手写的标准答案在你换命名之后会全部失效，而且手写本身就会出错。

---

## 第 6 步 · 装 Python 依赖并验证

```bash
make install     # uv 建虚拟环境到 ai/.venv，装依赖（首次 1-2 分钟）
make test        # SQL 护栏单元测试 —— 应该全绿
make eval-dry    # 评测集自检 —— 应该 20/20
make eval WEEK=1 # 应该失败：pipeline 还没实现。这就是 W1 的作业
```

最后一条失败是**预期的**，让它变绿就是第一周的任务。

**装完依赖后，告诉 VS Code 用哪个解释器**：
`Cmd+Shift+P` → `Python: Select Interpreter` → 选
`./ai/.venv/bin/python`。
选对了之后，左下角状态栏会显示 `.venv`，`from chemq import ...` 也不会再标红。

---

## VS Code 里怎么用

### 不用敲命令 —— 任务面板

`Cmd+Shift+P` → `Tasks: Run Task`，列表里有：

| 任务 | 等价命令 |
|---|---|
| doctor · 检查环境 | `make doctor` |
| up / down · 起停数据库 | `make up` / `make down` |
| seed · 重建数据 | `make seed` |
| test · 单元测试 | `make test` |
| eval · 跑评测 | 会弹出选第几周 |
| logs · 看数据库日志 | `make logs` |

`Cmd+Shift+B` 直接跑 doctor（默认任务）。

### 调试 —— 这是学习期最有用的功能

`F5` 或左侧「运行和调试」面板，有四个配置：

- **评测 · 本周** —— 在 `router.py` 里打断点，看每道题的解析过程
- **只看意图解析** —— 调 prompt 时用这个，只调 Haiku，几乎不花钱
- **问一句** —— 弹窗输入中文问题，走完整链路
- **FastAPI 服务** —— W5 才用

**强烈建议**：W1 写 `router.plan()` 时，在 `llm.py` 的 `parse()` 里打个断点，
看清楚 `system` 参数最终长什么样、`resp.usage` 里四个 token 数分别是多少。
比读十遍文档管用。

### 写 SQL 不用离开编辑器 —— SQLTools

左侧栏会多一个数据库图标，里面有两个连接：

- **ChemQ (只读)** —— 用 `chemq_ro_login` 账号，**和模型用的是同一个账号**。
  想知道模型能看到什么、看不到什么，就用这个连接试
- **ChemQ (superuser)** —— 建表用，平时别用

试一下这条，能直接看到数据里埋的现象：

```sql
SELECT * FROM chem.v_monthly_summary ORDER BY 1;
```

然后试这条 —— 应该报 `permission denied`：

```sql
SELECT * FROM chem.fact_sales LIMIT 10;
```

**这就是纵深防御的最后一层**：哪怕 `sqlguard` 有 bug 放过了一条查明细表的 SQL，
数据库这一关也过不去。

### Claude Code 扩展

装了 `anthropic.claude-code` 之后，`Cmd+Esc` 唤起。它会自动读项目根目录的
`CLAUDE.md`——里面写了**不许替你写四个核心模块的第一版**。
你卡住时它给线索不给代码，这是刻意的。

---

## 常用命令速查

```bash
make help        # 列出所有命令
make doctor      # 出问题先跑这个
make up / down   # 起 / 停数据库
make seed        # 改了 naming.yaml 之后跑
make psql        # 用只读账号进 psql
make sql Q="select ..."   # 跑一条 SQL 不进交互
make test        # 单元测试
make eval WEEK=1 # 跑评测
make logs        # 数据库日志
make reset       # 连数据一起删掉重来（慎用）
```

---

## Windows 用户

如果哪天换到 Windows：装 **WSL2 + Ubuntu**，在 WSL 里跑上面全部步骤，
VS Code 装 **WSL 扩展**后用 `code .` 从 WSL 里打开项目。
不要在 PowerShell 里直接跑——`make` 和路径都会出问题。
