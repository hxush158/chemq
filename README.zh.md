# ChemQ · 经营分析智能问数

一个**以学习为目的**的 AI 应用开发训练项目，练习场景是流程工业（化工）的经营分析问数。
6 周，每周 8–10 小时。产出两条线：你的能力，和一个可以交付/演示的系统。

> 业务命名目前是占位符，见 `naming.yaml`。换成实际命名后 `make seed` 即可。

## 30 分钟跑起来

前置依赖只有 **Docker Desktop** 和 **uv**。本机不需要装 PostgreSQL 客户端，也不需要动系统 Python。
逐步说明（含 VS Code 配置）见 **[`docs/02-setup-macos-vscode.md`](docs/02-setup-macos-vscode.md)**。

```bash
cp .env.example .env          # 填 CHEMQ_ANTHROPIC_API_KEY
make up                       # 起库；首次会自动建表 + 灌 12 个月数据
make doctor                   # 环境体检 —— 出问题先跑这个
make install                  # Python 依赖装到 ai/.venv
make test                     # SQL 护栏单元测试（应该全绿）
make eval-dry                 # 评测集自检（应该 20/20）
make eval WEEK=1              # 真实评测 —— 现在会失败，因为 pipeline 还没实现
```

最后一条失败是**预期的**。让它变绿就是 W1 的任务。

> `make seed` 首次不用跑 —— 数据已经在了。它是给"改了 `naming.yaml`"
> 或"想恢复干净状态"用的。

## 目录

```
naming.yaml         业务命名的唯一入口（占位符 → 实际命名）
db/                 schema · 语义层视图 · 只读角色与 RLS · 数据生成器
kb/                 指标口径字典（语义层的"人话"版本）
ai/src/chemq/       AI 服务层（带 TODO 的 stub 就是你的作业）
ai/solutions/       参考实现 —— 先自己写 30 分钟再翻
ai/evals/           评测集与跑分器 ← 这是学习闭环的核心
gateway/            .NET 网关（W5）
infra/              AWS CDK (C#)（W5）
docs/concepts/      概念卡（面试复习册）
docs/labs/          破坏实验
docs/journal/       学习笔记
.vscode/            VS Code 任务、调试配置、SQLTools 连接
```

## 学习纪律

1. **先自己写 30 分钟，再问 Claude。** 卡住的那 30 分钟才是学习发生的地方。
2. **每周先跑评测再改代码。** 不知道现在多少分，就不知道改动有没有用。
3. **破坏实验不能跳。** 看它怎么坏，比看它怎么对，信息量大得多。
4. **每周写一篇笔记。** 模板在 `docs/journal/TEMPLATE.md`，三个问题，20 分钟。

Claude Code 的具体使用边界写在 `CLAUDE.md` 里，它会遵守。

## 6 周路线

| 周 | 能力目标 | 达标线 |
|---|---|---|
| W1 | 看懂一次 API 调用的全部成本构成 | `intent_acc ≥ 0.90`，缓存命中可证 |
| W2 | 明白企业 Text-to-SQL 输在口径而非语法 | `exec_acc ≥ 0.80`，`refuse_acc = 1.00` |
| W3 | 能说清 RAG 每一环坏掉时的表现差异 | `citation_acc ≥ 0.90` |
| W4 | 讲清 Client SDK / Agent SDK / CLI 的边界 | 复杂问答端到端 + Critic 能抓错 |
| W5 | 把 AI 服务放上公网且账单可控 | 线上可访问，Budget 告警生效 |
| W6 | 用数据说明系统好在哪、何时不可信 | 60 题全量评测 + CI 回归 |

完整计划见 `docs/00-learning-plan.md`。
