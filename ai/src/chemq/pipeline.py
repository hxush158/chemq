"""端到端入口 —— 评测脚本调的就是这个函数。

W1 只要能返回 intent 就算过；之后每周往里加一层：
    W1  router.plan()                    -> intent
    W2  text2sql.generate_and_run()      -> sql / rows
    W3  rag.hybrid_search()              -> citations
    W4  agents.orchestrate()             -> 替换掉这里的手写流程
    W6  audit.write()                    -> 全链路留痕

TODO(W1): 实现最小版本 —— 调 router.plan()，把 intent 填进 Answer，
          out_of_scope 时 refused=True 并给出拒答理由。
参考实现见 ai/solutions/pipeline_w1.py（先自己写 30 分钟再翻）。
"""
from __future__ import annotations

import uuid

from .schemas import Answer
from .router import plan  # 需要引入写好的 plan 函数
from .text2sql import Unanswerable, generate_and_run

def answer(question: str, *, as_of: str = "2026-09-14", region: str | None = None) -> Answer:
    """as_of 是"今天"的固定值 —— 评测必须可复现，不能用 date.today()。"""
    
    trace_id = new_trace_id()
    
    # 调路由器拿 QueryPlan
    #result = plan(question)
    result = plan(f"基准时间：{as_of}\n用户问题：{question}")
    query_plan = result.parsed
    
    # 判断是否拒答
    # 白名单：只有在此集合内的意图才会被放行执行后续业务，其余一律 fail-closed 拒答
    
    ANSWERABLE_INTENTS = {
        "metric_query",
        "knowledge_lookup",
        "mixed",

    }
    is_refused = query_plan.intent not in ANSWERABLE_INTENTS
    
    if is_refused:
        return Answer(trace_id=trace_id, answer=query_plan.reasoning,
                      intent=query_plan.intent, refused=True,
                      cost_usd=result.usage.cost_usd)

    # 成本从 router 那一次开始累加。之前这里只记了 router（Haiku），
    # 生成 SQL 的 Sonnet 调用一分钱都没算 —— 而那才是大头。
    cost = result.usage.cost_usd

    sql, rows = None, []
    if query_plan.intent in ("metric_query", "mixed"):
        try:
            run = generate_and_run(
                query_plan, region=region,
                question=question, as_of=as_of,   # 原话透传：「最低」「两个」这些词 QueryPlan 里没有
            )
        except Unanswerable as e:
            # 拒答不是故障：它是一个合法的答案（"查不到"），要计分、要计成本。
            # router 漏判了、text2sql 兜住了 —— 这正是两层都要能拒答的意义。
            return Answer(
                trace_id=trace_id,
                answer=e.reason,
                intent=query_plan.intent,
                refused=True,
                sql=e.sql or None,
                cost_usd=cost + e.cost_usd,
                caveats=[f"text2sql 拒答（{e.kind}）"],
            )
        sql, rows = run.sql, run.rows
        cost += run.cost_usd

    return Answer(
        trace_id=trace_id,
        answer="",
        intent=query_plan.intent,
        refused=False,
        sql=sql,
        rows=rows,
        numbers_from_sql=bool(rows),
        cost_usd=cost,
    )

def new_trace_id() -> str:
    return str(uuid.uuid4())
