"""W4：用 Agent SDK 做一个"分析员 Agent" —— 它自己决定先查数还是先查资料、查几次。

和 pipeline.py（固定流程：router → text2sql → 知识库）回答的是同一类问题，评测时两边并排比。

三件工具的边界（W4 的核心）：
  Client SDK (anthropic)        你自己写每一步 —— pipeline.py 就是它
  Agent SDK  (claude_agent_sdk) SDK 替你跑"想 → 调工具 → 看结果 → 再想"的循环 —— 这个文件
  Claude Code CLI               终端里交互式用，挂上 mcp_server.py 就能直接查库

这里的每个设置都对应一条 W4 的教训：

  tools=[]              去掉 Claude Code 自带的全部工具（Bash、Read……），只剩我们的三个。
                        ⚠ allowed_tools 不是限制，它只是"这些不用问、直接放行"。
                          参考答案 solutions/agents_w4.py 写 allowed_tools=[] 以为"一个工具都不给"，是错的。
  setting_sources=[]    不读你电脑上的 ~/.claude 和项目的 .claude/ —— 否则评测结果取决于谁的电脑在跑
  max_turns / max_budget_usd
                        Agent 循环的终止条件是模型自己判断"做完了"，所以必须有外部上限
  PreToolUse hook       每次调用工具前记审计日志；不在白名单里的工具一律拒绝（tools=[] 之外的第二道）
  工具内部的 sqlguard     第三道：就算前两道都漏了，SQL 照样要过护栏（见 tools.py）
  Critic（critic.py）   回答里的每个数字都必须出现在工具真实返回的数据里

scope（行级权限）在建工具时绑定，不是工具参数，模型改不了。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import critic, tools
from .config import settings
from .domain import domain
from .kb_answer import NO_CITATION_TEXT
from .prompts import render
from .schemas import Answer, Citation

SERVER = "chemq"
TOOL_NAMES = ("list_metrics", "run_semantic_query", "search_knowledge")
ALLOWED = tuple(f"mcp__{SERVER}__{n}" for n in TOOL_NAMES)
CANNOT_ANSWER = "【无法回答】"

MAX_TURNS = 8
MAX_BUDGET_USD = 0.10

# 提示词正文在 prompts/<locale>/agent.md；{as_of}、{data_range} 是单花括号，orchestrate() 每次调用时才 format。
SYSTEM_PROMPT = render("agent")


@dataclass
class AgentRun:
    """一次 orchestrate 的全部留痕。Answer 是给前端的，这个是给评测和复盘的。"""

    answer: Answer
    tool_calls: list[dict[str, Any]] = field(default_factory=list)   # 审计日志（hooks 写的）
    tools_available: list[str] = field(default_factory=list)          # init 消息里 Agent 实际看到的工具
    num_turns: int = 0
    stop: str = ""                                                    # success / error_max_turns / ...
    rows_all: list[dict[str, Any]] = field(default_factory=list)      # 所有成功查询的结果行（Critic 的数据源）
    cited_texts: list[str] = field(default_factory=list)              # 标了出处的资料原文（Critic 的数据源）


class Recorder:
    """工具真实返回了什么 —— Critic 核对数字、评测比对结果、前端展示引用，都以它为准，
    不以模型"说"它查到了什么为准。"""

    def __init__(self) -> None:
        self.sql_runs: list[dict[str, Any]] = []
        self.kb_hits: list[dict[str, Any]] = []
        self.audit: list[dict[str, Any]] = []

    @property
    def last_ok_sql(self) -> dict[str, Any] | None:
        ok = [r for r in self.sql_runs if r.get("ok")]
        return ok[-1] if ok else None

    def all_rows(self) -> list[dict[str, Any]]:
        return [row for r in self.sql_runs if r.get("ok") for row in r["rows"]]

    def citations(self, answer: str) -> list[Citation]:
        """只算回答里**标了编号**的资料。检索到的是候选，标出来的才是引用 ——
        第一版把检索到的 8 条全算成了引用，"第一条 目的"这种没用上的也在里面，
        评测的关键词检查会因此虚高。"""
        seen: dict[int, Citation] = {}
        for h in self.kb_hits:
            if h.get("ref") and h["ref"] in answer:
                seen.setdefault(h["chunk_id"], Citation(
                    doc_id=h["doc_id"], title=h["source"], chunk_id=h["chunk_id"], snippet=h["text"]))
        return list(seen.values())


def _text(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


def build_tools(rec: Recorder, scope: str | None) -> list[Any]:
    """三个工具包成 SDK 的进程内 MCP 工具。实现全在 tools.py，这里只做记录和格式转换。
    tools.py 里是同步的数据库 / Titan 调用，用 to_thread 放到线程里跑，不堵事件循环。"""
    from claude_agent_sdk import tool  # noqa: PLC0415

    @tool("list_metrics", tools.DESC_LIST_METRICS, {})
    async def list_metrics(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": await asyncio.to_thread(tools.list_metrics)}]}

    @tool("run_semantic_query", tools.DESC_RUN_SQL, {"sql": str})
    async def run_semantic_query(args: dict[str, Any]) -> dict[str, Any]:
        r = await asyncio.to_thread(tools.run_semantic_query, args["sql"], scope=scope)
        rec.sql_runs.append(r)
        out = _text(r)
        if not r["ok"]:
            out["is_error"] = True
        return out

    @tool("search_knowledge", tools.DESC_SEARCH, {"question": str})
    async def search_knowledge(args: dict[str, Any]) -> dict[str, Any]:
        r = await asyncio.to_thread(tools.search_knowledge, args["question"])
        rec.kb_hits.extend(r.get("hits", []))
        return _text(r)

    return [list_metrics, run_semantic_query, search_knowledge]


def make_hooks(rec: Recorder) -> dict[str, Any]:
    """PreToolUse：记审计 + 白名单之外的工具一律拒绝。PostToolUse：记结束时间。
    hooks 解决"做了什么"（可复盘），tools=[] 解决"能做什么"（事前约束）—— 两者不能互相替代。"""
    from claude_agent_sdk import HookJSONOutput, HookMatcher  # noqa: PLC0415

    # 返回类型要标成 SDK 的 HookJSONOutput（TypedDict），标 dict[str, Any] 时 Pylance 报
    # "dict[str, Any] is not assignable to HookJSONOutput"。运行时没区别，只是类型检查。

    async def pre(input_data: Any, tool_use_id: str | None, context: Any) -> HookJSONOutput:
        name = input_data.get("tool_name", "")
        allowed = name in ALLOWED
        rec.audit.append({"id": tool_use_id, "tool": name, "input": input_data.get("tool_input"),
                          "t0": time.time(), "allowed": allowed})
        if not allowed:
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{name} 不在允许的工具列表里，只能用 {', '.join(TOOL_NAMES)}",
            }}
        return {}

    async def post(input_data: Any, tool_use_id: str | None, context: Any) -> HookJSONOutput:
        for a in reversed(rec.audit):
            if a["id"] == tool_use_id:
                a["ms"] = int((time.time() - a["t0"]) * 1000)
                break
        return {}

    return {"PreToolUse": [HookMatcher(hooks=[pre])], "PostToolUse": [HookMatcher(hooks=[post])]}


def infer_intent(rec: Recorder, refused: bool) -> str:
    """Agent 不输出意图标签，按它实际做了什么反推，评测的 intent_acc 才有可比对象。"""
    if refused:
        return "out_of_scope"
    used_sql = any(a["tool"].endswith("run_semantic_query") for a in rec.audit)
    used_kb = any(a["tool"].endswith("search_knowledge") for a in rec.audit)
    if used_sql and used_kb:
        return "mixed"
    if used_kb:
        return "knowledge_lookup"
    return "metric_query"


def to_answer(text: str, rec: Recorder, *, cost: float, stop: str, question: str = "") -> Answer:
    refused = CANNOT_ANSWER in text
    shown = text.replace(CANNOT_ANSWER, "").strip()
    last = rec.last_ok_sql
    caveats: list[str] = []
    if stop and stop != "success":
        caveats.append(f"Agent 没有正常结束：{stop}")

    intent = infer_intent(rec, refused)
    cites = [] if refused else rec.citations(shown)
    # 和固定流程（kb_answer.py）同一条规则：纯知识题答了却一条出处都没标 → 拦下。
    # 混合题数字部分还能用，只加提醒。
    if not refused and rec.kb_hits and not cites:
        if intent == "knowledge_lookup":
            refused, shown = True, NO_CITATION_TEXT
            caveats.append("回答没有标出资料编号，已拦截（no_citation）")
        elif intent == "mixed":
            caveats.append("原因解释部分没有标出资料编号，未经引用核实")

    verdict = critic.check(
        shown, rec.all_rows(),
        texts=[c.snippet for c in cites],           # 只认标了出处的资料：没标就用了它的数字，也算没依据
        question=question,                          # 用户自己说的数字也算依据
    ) if not refused else critic.Verdict(ok=True)
    if not verdict.ok:
        caveats.append("这些数字在查询结果和引用原文里都找不到：" + "、".join(verdict.unverified))

    return Answer(
        trace_id=str(uuid.uuid4()),
        answer=shown,
        intent=intent,                              # type: ignore[arg-type]
        refused=refused or (stop != "success"),
        cost_usd=cost,
        sql=None if refused or last is None else last["sql"],
        rows=[] if refused or last is None else last["rows"],
        citations=cites,
        numbers_from_sql=verdict.ok,
        needs_human_review=not verdict.ok,
        caveats=caveats,
    )


async def orchestrate(question: str, *, as_of: str, scope: str | None = None) -> AgentRun:
    from claude_agent_sdk import (  # noqa: PLC0415
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        SystemMessage,
        TextBlock,
        create_sdk_mcp_server,
        query,
    )

    from .router import data_range  # noqa: PLC0415

    s = settings()
    rec = Recorder()
    # SDK 不读 .env，只认 ANTHROPIC_API_KEY；我们的 key 叫 CHEMQ_ANTHROPIC_API_KEY，显式传进去。
    # 只有 3 个工具，关掉 tool search：否则工具定义被延迟加载，每题多一轮"先搜工具"。
    env = {"ENABLE_TOOL_SEARCH": "false"}
    if s.anthropic_api_key:
        env["ANTHROPIC_API_KEY"] = s.anthropic_api_key
    # 破坏实验 C：不传 tools=[]，Agent 能看到 Claude Code 自带的全部工具（Bash、Read……）。
    # PreToolUse hook 不动 —— 验证"第一道防线拿掉后，第二道还在"。只在实验时打开。
    experiment_c = os.environ.get("CHEMQ_AGENT_EXPERIMENT_C") == "1"
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT.format(as_of=as_of, data_range=data_range()),
        tools=None if experiment_c else [],         # 去掉全部自带工具
        mcp_servers={SERVER: create_sdk_mcp_server(name=SERVER, tools=build_tools(rec, scope))},
        allowed_tools=list(ALLOWED),                # 只是"不用问"，不是限制
        setting_sources=[],
        max_turns=MAX_TURNS,
        max_budget_usd=MAX_BUDGET_USD,
        model=s.model_sql,
        env=env,
        hooks=make_hooks(rec),                      # type: ignore[arg-type]
    )

    texts: list[str] = []
    result: Any = None
    available: list[str] = []
    try:
        async for msg in query(prompt=question, options=options):
            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                available = list(msg.data.get("tools", []))
            elif isinstance(msg, AssistantMessage):
                texts += [b.text for b in msg.content if isinstance(b, TextBlock)]
            elif isinstance(msg, ResultMessage):
                result = msg
    except Exception as e:  # noqa: BLE001 —— 超轮数/超预算时 SDK 在给出结果后会抛异常
        if result is None:
            raise RuntimeError(f"Agent 运行失败：{type(e).__name__}: {e}") from e

    stop = getattr(result, "subtype", "") or "no_result"
    final = (getattr(result, "result", None) or (texts[-1] if texts else "")).strip()
    cost = float(getattr(result, "total_cost_usd", 0.0) or 0.0)
    ans = to_answer(final, rec, cost=cost, stop=stop, question=question)
    return AgentRun(
        answer=ans,
        rows_all=rec.all_rows(),
        cited_texts=[c.snippet for c in ans.citations],
        tool_calls=rec.audit,
        tools_available=available,
        num_turns=int(getattr(result, "num_turns", 0) or 0),
        stop=stop,
    )


def answer(question: str, *, as_of: str | None = None, scope: str | None = None) -> Answer:
    """同步入口，签名和 pipeline.answer 一样 —— 评测脚本可以无缝切换两种做法。as_of 不传就用领域包的。"""
    return asyncio.run(orchestrate(question, as_of=as_of or domain().as_of, scope=scope)).answer


def main(argv: list[str] | None = None) -> int:
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser(description="用 Agent SDK 回答一个问题，打印调用过程")
    ap.add_argument("question")
    ap.add_argument("--as-of", default=None, help="基准时间，默认取领域包 eval.as_of")
    ap.add_argument("--scope", default=None)
    a = ap.parse_args(argv)

    if os.environ.get("CHEMQ_AGENT_EXPERIMENT_C") == "1":
        print("⚠ 破坏实验 C：没有传 tools=[]，Agent 能看到全部自带工具；只靠 PreToolUse hook 拦\n")
    run = asyncio.run(orchestrate(a.question, as_of=a.as_of or domain().as_of, scope=a.scope))
    shown = run.tools_available if len(run.tools_available) <= 6 else (
        run.tools_available[:12] + [f"…共 {len(run.tools_available)} 个"])
    print(f"Agent 看到的工具：{shown}")
    print(f"结束方式：{run.stop}   轮数：{run.num_turns}   成本：${run.answer.cost_usd:.4f}\n")
    for i, c in enumerate(run.tool_calls, 1):
        mark = "" if c["allowed"] else "  ← 被 hook 拒绝"
        print(f"  {i}. {c['tool']}  {json.dumps(c['input'], ensure_ascii=False)[:160]}  {c.get('ms', '?')}ms{mark}")
    ans = run.answer
    print(f"\n{'【拒答】' if ans.refused else ''}{ans.answer}\n")
    if ans.citations:
        print("引用：" + "；".join(c.title for c in ans.citations))
    print(f"Critic：{'数字全部核对通过' if not ans.needs_human_review else '需要人工复核'}")
    for c in ans.caveats:
        print(f"  ! {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
