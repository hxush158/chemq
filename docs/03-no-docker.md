# 没有 Docker 怎么办

Docker Desktop 现在要求 macOS 13 (Ventura) 以上。老机器装不了，不影响开始。

## 先说结论

| 周 | 需要数据库吗 | 老机器能不能做 |
|---|---|---|
| **W1** | **不需要** | ✅ 完全可以，现在就开始 |
| W2 | 需要 PostgreSQL | ⚠️ 用下面的方案 A 或 B |
| W3 | 需要 PostgreSQL + pgvector | ⚠️ 方案 A |
| W4 | 同上 | ⚠️ 方案 A |
| W5–W6 | 上云，本地无所谓 | ✅ |

**W1 为什么不需要数据库**：这一周的全部内容是意图路由——把中文问题解析成
`QueryPlan` 结构体。调用链是 `pipeline.answer()` → `router.plan()` → `llm.parse()`，
里面一行 psycopg 都没有。评测集 `golden.jsonl` 里的标准答案是**之前跑数据库生成好、
已经提交进仓库的**，跑评测只读这个文件。

```bash
make install       # 最小依赖，不含 psycopg
make doctor-w1     # W1 专用体检，不检查 Docker
make test          # SQL 护栏单测（sqlglot 是纯 Python，不需要数据库）
make eval-dry      # 评测集自检
make eval WEEK=1   # 开始干活
```

---

## 方案 A · 云端 PostgreSQL（推荐）

**Neon** 或 **Supabase** 的免费套餐，本机零安装，而且给的是**真** PostgreSQL：
pgvector、只读角色、行级权限都在，W2/W3 的知识点一个都不打折。
pgvector 在 Neon 的所有套餐（含免费）都可用，一句 `CREATE EXTENSION vector;` 就开。

**步骤**

1. 注册 <https://neon.com>（或 <https://supabase.com>），建一个项目，拿到连接串
2. 在它的 Web SQL 编辑器里跑一句 `CREATE EXTENSION IF NOT EXISTS vector;`
3. 改 `.env`：

   ```
   CHEMQ_PG_DSN=postgresql://<user>:<pass>@<host>/<db>?sslmode=require
   CHEMQ_PG_DSN_READONLY=postgresql://chemq_ro_login:<pass>@<host>/<db>?sslmode=require
   ```

4. `make install-db`
5. 把 `domains/chem/data/01_schema.sql` … `04_roles.sql` 的内容依次贴进 Web SQL 编辑器执行
   （或用 `make seed SQLDIR=db PSQL=psql`，前提是本机有 psql）

**要注意的地方**（这些本身就是 W2 的内容）：

- 托管 PostgreSQL 给你的不是真 superuser。`CREATE ROLE` 一般可以，
  但个别 `ALTER ... OWNER` 可能被拒——遇到了就记进学习笔记，
  **"在托管数据库上怎么做权限隔离"是个很实际的企业问题**
- 免费套餐会自动休眠，第一次查询有几百毫秒到几秒的唤醒延迟。
  这正好是 W5 冷启动那节课的预演
- 连接串里有密码，**只放 `.env`，别提交**

**代价**：网络延迟。本地 Docker 查一次 5ms，云端可能 100–300ms。
对 W2/W3 的学习没影响（瓶颈在模型调用，不在数据库）。

---

## 方案 B · conda 装本地 PostgreSQL（不联网也能用）

你机器上已经有 miniconda3 / anaconda3，可以直接用它装一个不需要管理员权限、
不依赖 Docker 的 PostgreSQL：

```bash
conda create -n chemq-db -c conda-forge postgresql -y
conda activate chemq-db

initdb -D ~/chemq-pgdata -U postgres
pg_ctl -D ~/chemq-pgdata -l ~/chemq-pgdata/server.log start

createdb -U postgres chemq
psql -U postgres -d chemq -f domains/chem/data/01_schema.sql   # 见下方注意事项
```

`.env` 改成：

```
CHEMQ_PG_DSN=postgresql://postgres@localhost:5432/chemq
```

然后 `make seed PSQL=psql SQLDIR=db`。

**注意事项**：conda-forge 的 postgresql **不带 pgvector**。所以这条路：

- ✅ W2 完全够用（语义层、护栏、只读角色、RLS 都不需要 pgvector）
- ❌ W3 的向量检索跑不了 —— 到那时要么换方案 A，要么新机器已经到了

W2 之前先把 `domains/chem/data/01_schema.sql` 里这三行注释掉：

```sql
-- CREATE EXTENSION IF NOT EXISTS vector;
-- embedding       vector(1024),         改成  embedding real[],
-- CREATE INDEX kb_chunk_vec_idx ...
```

停库：`pg_ctl -D ~/chemq-pgdata stop`

---

## 方案 C · 等新机器，先做不需要机器的事

有相当一部分工作**根本不需要代码环境**，而且价值不低：

| 事情 | 时长 | 为什么值得先做 |
|---|---|---|
| 把指标口径字典换成目标客户**真实**口径 | 3–5h | 这是整个项目准确率的第一杠杆，也是**对目标客户最有价值的交付物**，和 AI 无关 |
| 评测集从 20 题扩到 60 题 | 2–3h | 纯写题，不用跑。四个象限：取数 / 知识 / 混合 / **该拒答** |
| 读 Agent SDK 官方文档 | 1h | W4 的概念课，读完再动手效率高得多 |
| 注册 AWS + 建 $10 Budget 告警 | 20min | 浏览器操作。W5 之前必须有 |
| 在 Console Workbench 里手动试 prompt | 1–2h | <https://platform.claude.com/workbench> 不用写代码就能试 system/few-shot/structured outputs |
| 通读 `domains/chem/data/03_semantic_views.sql` | 1h | 特别是 `v_monthly_summary` 的扇出陷阱注释 |

**其中第一项最值钱。** 指标口径字典（`domains/chem/metric_dictionary.yaml`）现在写的是我编的
通用化工口径。换成目标客户实际的——「产量」到底含不含返工？「综合能耗」的折标系数用哪一版？
「回款率」和财务口径差在哪？——这份东西哪怕智能问数最后不上线，
数据治理也用得上，而且它需要的是你的行业经验，不是编程能力。
