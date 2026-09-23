"""W4 交付物：多 Agent 编排。

这一周的重点不是"把活干完"，是搞清楚三件工具的边界：

  Client SDK (anthropic)        你自己写 tool loop —— W1/W2 用的就是它
  Agent SDK  (claude_agent_sdk) SDK 替你跑 agent loop，带 subagent/hooks/permissions/MCP
  Claude Code CLI               终端里交互式开发，可以挂你自己的 MCP server 直接调库

编排结构（刻意做成这样，因为它对应一条真实的业务约束）：

    问题 ──> Router ──┬──> SQL Agent      ──┐
                      ├──> Knowledge Agent ─┼──> Critic ──> Narrator ──> 回答
                      └──> Chart Agent    ──┘

  · Critic 单独一个 Agent：它只做一件事 —— 核对 Narrator 引用的每个数字
    是否真的出现在 SQL 结果里。这是"数值由 SQL 算、模型不算数"这条原则的执行者。
  · Narrator 拿不到数据库连接，只拿到 SQL 结果。工具权限即是防线。

验收：
  · Q009 / Q020 这类"既要数又要解释"的题能端到端答对
  · Critic 能抓出注入的错误数字（见 docs/labs/w4.md 的破坏实验）
  · 单次复杂问答成本 < $0.05

TODO(W4):
  1. 用 claude_agent_sdk 的 query() + ClaudeAgentOptions 搭 orchestrate()
  2. 用 allowed_tools 严格限制每个 Agent 能碰什么
  3. 用 hooks 在每次 tool 调用前后写审计日志
参考实现见 ai/solutions/agents_w4.py
"""
from __future__ import annotations

from .schemas import Answer


async def orchestrate(question: str, *, as_of: str, region: str | None = None) -> Answer:
    raise NotImplementedError("W4 任务：用 Agent SDK 实现编排")
