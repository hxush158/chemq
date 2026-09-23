"""【参考实现 · W1】pipeline.answer 的最小版本。

先自己写 30 分钟再看这里。看之前问自己三个问题：
  1. 为什么 as_of 要当参数传进来，而不是在函数里调 date.today()?
  2. out_of_scope 的拒答理由，应该由模型生成还是代码写死？为什么？
  3. usage 记账放在哪一层最合适？
"""
from __future__ import annotations

import uuid

from chemq.llm import Llm
from chemq.router import plan
from chemq.schemas import Answer

REFUSAL_TEMPLATES = {
    "out_of_scope": "这个问题超出了当前数据范围。{reason}。可提供的指标见指标口径字典。",
}


def answer(question: str, *, as_of: str = "2026-09-14", region: str | None = None) -> Answer:
    trace_id = str(uuid.uuid4())
    llm = Llm()

    # as_of 作为参数：评测要可复现。用 date.today() 的话，今天跑和下个月跑答案不一样，
    # 评测分数的波动就分不清是模型变了还是日期变了。
    res = plan(f"[今天是 {as_of}]\n{question}", llm=llm)
    p = res.parsed

    if p.intent in ("out_of_scope", "chitchat"):
        # 拒答理由让模型给（p.reasoning），但拒答这个"决定"由代码做。
        # 反过来会出问题：模型在同一次调用里既判断又措辞时，容易为了把话说圆而改判断。
        return Answer(
            trace_id=trace_id,
            answer=REFUSAL_TEMPLATES["out_of_scope"].format(reason=p.reasoning),
            intent=p.intent,                    # ← 评测靠它算 intent_acc
            refused=True,                       # ← 评测靠它算 refuse_acc
            cost_usd=res.usage.cost_usd,        # ← 评测靠它汇总成本
            numbers_from_sql=True,
            rows=[],
            caveats=["已拒答：问题不在系统数据范围内"],
            needs_human_review=False,
        )

    # W1 还没接 SQL，先把结构化解析的结果透出来，让评测能打 intent 分
    return Answer(
        trace_id=trace_id,
        answer=f"（W1 阶段）解析结果：{p.model_dump_json(indent=2)}",
        intent=p.intent,
        refused=False,
        cost_usd=res.usage.cost_usd,
        rows=[],
        caveats=["W2 接入 Text-to-SQL 后本函数才会返回真实数字"],
        needs_human_review=True,
    )


# usage 记账放在 Llm 里（每次调用都记），不要放在 pipeline 里（会漏掉重试和子调用）。
