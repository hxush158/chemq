"""【参考实现 · W4】Agent SDK 编排 + Critic。

看之前先回答：
  1. Narrator 为什么不给数据库工具？如果给了会发生什么？
  2. Critic 用什么模型？比 Narrator 强还是弱？为什么？
  3. hooks 和 permissions 分别解决什么问题？能互相替代吗？
"""
from __future__ import annotations

import json
import re

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, query

from chemq.config import settings

# 决定 1：Narrator 只拿到 SQL 结果的 JSON，没有任何数据库工具。
# 给了会怎样：模型发现叙述和数据对不上时，会"再查一次"把数据凑到叙述上 ——
# 这正好倒转了因果。工具权限的边界就是职责的边界，这比在 prompt 里写
# "请不要自行查询" 可靠得多。prompt 是建议，权限是约束。

NARRATOR_SYSTEM = """你是经营分析报告撰写者。
你只能使用下面给出的 SQL 查询结果中出现的数字，一个字都不能改、不能推算、不能补全。
如果结论需要某个数字而结果里没有，就直接写"该数据不可得"。
输出 3 段：结论 / 依据 / 需要注意的口径问题。"""

CRITIC_SYSTEM = """你是数字核对员。给你一段分析结论和一份 SQL 结果集。
逐一提取结论中出现的每个数字，检查它是否精确出现在结果集中（允许四舍五入到同一位）。
输出 JSON: {"ok": bool, "unverified": [{"number": "...", "context": "..."}]}
不要评价结论写得好不好，只核对数字。"""


async def orchestrate(question: str, *, as_of: str, region: str | None = None) -> dict:
    from chemq.router import plan
    from chemq.text2sql import Unanswerable, generate_and_run

    p = plan(f"[今天是 {as_of}]\n{question}").parsed
    if p.intent in ("out_of_scope", "chitchat"):
        return {"refused": True, "reason": p.reasoning}

    try:
        run = generate_and_run(p, region=region, question=question, as_of=as_of)
    except Unanswerable as e:
        return {"refused": True, "reason": e.reason}
    sql, rows, candidate = run.sql, run.rows, run.candidate

    narration = ""
    async for msg in query(
        prompt=f"问题：{question}\n\nSQL 结果：\n{json.dumps(rows, ensure_ascii=False)}",
        options=ClaudeAgentOptions(
            system_prompt=NARRATOR_SYSTEM,
            allowed_tools=[],          # ← 一个工具都不给
            permission_mode="default",
            model=settings().model_sql,
        ),
    ):
        if isinstance(msg, AssistantMessage):
            narration += "".join(getattr(b, "text", "") for b in msg.content)
        elif isinstance(msg, ResultMessage):
            break

    verdict = await _critic(narration, rows)
    return {
        "refused": False, "sql": sql, "rows": rows, "answer": narration,
        "metric_definitions_used": candidate.metric_definitions_used,
        "needs_human_review": not verdict.get("ok", False),
        "unverified_numbers": verdict.get("unverified", []),
    }


async def _critic(narration: str, rows: list[dict]) -> dict:
    # 决定 2：Critic 用和 Narrator 同级或更弱的模型就够，因为核对数字是机械任务，
    # 不需要推理能力。更重要的是它必须是**独立的一次调用**，不共享上下文 ——
    # 同一次调用里让模型"自我检查"，它会倾向于维护自己刚说过的话。
    # 独立调用 + 只看两份材料，才有对抗性。
    out = ""
    async for msg in query(
        prompt=f"结论：\n{narration}\n\n结果集：\n{json.dumps(rows, ensure_ascii=False)}",
        options=ClaudeAgentOptions(
            system_prompt=CRITIC_SYSTEM, allowed_tools=[],
            model=settings().model_critic,
        ),
    ):
        if isinstance(msg, AssistantMessage):
            out += "".join(getattr(b, "text", "") for b in msg.content)
    m = re.search(r"\{.*\}", out, re.S)
    return json.loads(m.group()) if m else {"ok": False, "unverified": []}


# 决定 3：
#   permissions 是"能不能做" —— 事前拦截，不可绕过。
#   hooks 是"做了什么" —— 事中/事后观察，可以记录、可以告警、也可以否决。
# 不能互相替代：只有 hooks 没有 permissions，等于全靠日志发现问题，来不及；
# 只有 permissions 没有 hooks，你不知道 Agent 到底调了什么，出了问题无法复盘。
# 审计要求高的企业场景里两个都得有 —— 这也是 qa_audit 表存在的理由。
