# ChemQ — 给 Codex 的项目规则

这是一个**学习项目**。目标是 Hui 掌握 AI 应用开发，不是尽快把系统做出来。
请按下面的规则协助，这些规则的目的是保护学习效果。

## 你不要做的事

- **不要替我写这四个模块的第一版实现**：
  `ai/src/chemq/router.py` · `text2sql.py` · `rag.py` · `agents.py`
  我卡住时，给线索、给反问、给排查方向，**不要给代码**。
  我明确说"给我看参考实现"时，让我去翻 `ai/solutions/`，而不是你现场写一份。
- 不要主动重构我写的代码。我没让你改，就说明我还在想。
- 不要在我没问的时候解释我已经写对的部分。

## 你可以放手做的事

- 样板代码：SQL schema、docker-compose、CI、Dockerfile、CDK
- 我写完之后的 **code review**：指出 bug、安全问题、口径错误
- 出练习题：针对某个概念，给我 3 道能暴露理解缺口的题
- 解释概念：但优先用本项目里的代码举例，不要用通用示例
- 查官方文档并给我准确的 API 用法（我的知识可能过时）

## Review 时重点看什么

1. **数字是不是全部来自 SQL** —— 任何模型参与算数的地方都要标红
2. **口径有没有绕过语义层** —— SQL 里出现硬编码系数、直接查 `fact_*` 表就是错
3. **拒答路径** —— 该拒答的地方有没有降级成"猜一个"
4. **成本** —— 有没有把稳定内容放进 messages 导致缓存失效

## 项目约定

- 业务命名全部来自 `domains/chem/data/seed.yaml`，代码和文档里不写死装置/产品名
- 指标口径唯一来源是 `domains/chem/metric_dictionary.yaml` + `domains/chem/data/03_semantic_views.sql`
- 改了 `domains/chem/data/seed.yaml` 必须 `make seed`（会重建数据并重算评测集标准答案）
- 随机种子固定在 `domains/chem/data/gen_seed.py`，不要改 —— 评测集的标准答案依赖它
- 提交前：`make test && make eval-dry`

## 当前进度

W0–W4 ✅（两引擎、RAG、MCP、Critic、评测）| 原 W5/W6 已被阶段二替代

阶段二（引擎层，见 `docs/10-flagship-plan.md`）：W1 ⬜ 领域包 + DuckDB | W2 ⬜ Snowflake | W3 ⬜ NZ 领域包 + 评估集 | W4 ⬜ 部署 + 演示
