"""端到端入口 —— 评测脚本调的就是这个函数。

W1 只要能返回 intent 就算过；之后每周往里加一层：
    W1  router.plan()                    -> intent
    W2  text2sql.generate_and_run()      -> sql / rows
    W3  kb_answer.answer_from_knowledge() -> answer + citations（✅；NZ W3 起知识库 + 指标字典）
    W4  agents.orchestrate()             -> 替换掉这里的手写流程
    W6  audit.write()                    -> 全链路留痕

TODO(W1): 实现最小版本 —— 调 router.plan()，把 intent 填进 Answer，
          out_of_scope 时 refused=True 并给出拒答理由。
参考实现见 ai/solutions/pipeline_w1.py（先自己写 30 分钟再翻）。
"""
from __future__ import annotations

import uuid

from .coverage import check_time, load_coverage
from .domain import domain
from .kb_answer import answer_from_knowledge
from .prompts import string
from .router import plan  # 需要引入写好的 plan 函数
from .schemas import Answer
from .text2sql import Unanswerable, generate_and_run


def answer(question: str, *, as_of: str | None = None, scope: str | None = None) -> Answer:
    """as_of 是"今天"的固定值 —— 评测必须可复现，不能用 date.today()。不传就用领域包的 eval.as_of。"""
    as_of = as_of or domain().as_of
    
    trace_id = new_trace_id()
    
    # 调路由器拿 QueryPlan
    #result = plan(question)
    result = plan(string("router_user_message", extra={"as_of": as_of, "question": question}))
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
    caveats: list[str] = []
    notes: list[str] = []
    
    sql, rows = None, []
    if query_plan.intent in ("metric_query", "mixed"):
        # Router 提取时间后，由代码检查覆盖范围，再决定是否生成 SQL。
        verdict = check_time(query_plan, load_coverage())
        if verdict.action == "refuse":
            return Answer(
                trace_id=trace_id,
                answer=verdict.reason or string("time_unavailable"),
                intent="out_of_scope",
                refused=True,
                cost_usd=cost,
                caveats=[string("caveat_time_refused")],
            )
        if verdict.action == "clip":
            caveats.append(string("caveat_time_clipped", extra={"reason": verdict.reason}))
        query_plan = verdict.plan

        try:
            run = generate_and_run(
                query_plan, scope=scope,
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
                caveats=caveats + [string("caveat_sql_refused", extra={"kind": e.kind})],
            )
        sql, rows = run.sql, run.rows
        cost += run.cost_usd

        notes = list(run.candidate.assumptions)

        if query_plan.time_range is not None:
            notes.append(
                string(
                    "note_date_range",
                    extra={
                        "start": query_plan.time_range.start,
                        "end": query_plan.time_range.end,
                    }
                )
            )

        if not rows:
            # 0 行是正常回答（"这段时间没有数据"），不是拒答，也不是数值为 0。
            # mixed 也在这里结束：没有数字，就没有可解释的对象，不再花钱调知识库。
            return Answer(
                trace_id=trace_id,
                answer=string("no_data"),
                intent=query_plan.intent,
                refused=False,
                no_data=True,
                sql=sql,
                rows=[],
                notes=notes,
                numbers_from_sql=False,
                cost_usd=cost,
                caveats=caveats,
            )

    # 知识类、混合类走资料来源（有知识库的领域：知识库 + 指标字典；没有的：只有指标字典）。
    # 混合类把 SQL 结果一起交给 Claude，数字只许照抄。
    if query_plan.intent in ("knowledge_lookup", "mixed"):
        kb = answer_from_knowledge(question, sql_rows=rows if query_plan.intent == "mixed" else None)
        cost += kb.cost_usd

        if query_plan.intent == "knowledge_lookup":
            return Answer(
                trace_id=trace_id,
                answer=kb.answer,
                intent=query_plan.intent,
                refused=kb.refused,
                citations=kb.citations,
                numbers_from_sql=False,
                cost_usd=cost,
                caveats=caveats + ([string("caveat_kb_refused", extra={"kind": kb.kind})] if kb.refused else []),
            )

        # mixed：数字已经有了，知识库答不上只是少了解释，不算整体拒答
        return Answer(
            trace_id=trace_id,
            answer="" if kb.refused else kb.answer,
            intent=query_plan.intent,
            refused=False,
            sql=sql,
            rows=rows,
            notes=notes,
            citations=kb.citations if not kb.refused else [],
            numbers_from_sql=bool(rows),
            cost_usd=cost,
            caveats=caveats + (
                [string("caveat_kb_no_explanation", extra={"kind": kb.kind, "detail": kb.answer})] if kb.refused else []
            ),
        )

    return Answer(
        trace_id=trace_id,
        answer="",
        intent=query_plan.intent,
        refused=False,
        sql=sql,
        rows=rows,
        notes=notes,
        numbers_from_sql=bool(rows),
        cost_usd=cost,
        caveats=caveats,
    )

def new_trace_id() -> str:
    return str(uuid.uuid4())
