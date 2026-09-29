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
    
    # 按照 Answer 的字段完全对齐实例化
    return Answer(
        trace_id=trace_id,
        answer=query_plan.reasoning if is_refused else "",  # 填入 answer 字段
        intent=query_plan.intent,                           # 填入 intent 字段
        refused=is_refused,                                 # 填入 refused 字段
        cost_usd=result.usage.cost_usd,                       # 填入 cost_usd 字段段
    )

def new_trace_id() -> str:
    return str(uuid.uuid4())
