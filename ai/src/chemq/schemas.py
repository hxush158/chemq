"""所有跨层数据结构。

刻意用 pydantic 定义，因为它们同时是 Claude structured outputs 的 JSON Schema 来源：
    client.messages.parse(..., output_format=QueryPlan)
"""
from typing import Literal
from pydantic import BaseModel, Field

Intent = Literal["metric_query", "knowledge_lookup", "mixed", "chitchat", "out_of_scope"]


class TimeRange(BaseModel):
    start: str = Field(description="ISO 日期 YYYY-MM-DD")
    end: str = Field(description="ISO 日期 YYYY-MM-DD，闭区间")
    grain: Literal["day", "month", "quarter", "year"] = "month"


class QueryPlan(BaseModel):
    """W1 产出：把中文问题结构化。不含 SQL —— 意图和取数分两步，便于单独评测。"""
    intent: Intent
    metrics: list[str] = Field(default_factory=list, description="指标字典中的标准指标名")
    dimensions: list[str] = Field(default_factory=list, description="如 装置/产品/大区/客户")
    filters: dict[str, str] = Field(default_factory=dict)
    time_range: TimeRange | None = None
    needs_knowledge: bool = Field(description="是否需要查制度/口径文档")
    reasoning: str = Field(description="一句话说明判断依据")


class SqlCandidate(BaseModel):
    """W2 产出。"""
    sql: str
    metric_definitions_used: list[str] = Field(
        default_factory=list, description="引用了指标字典中的哪些口径条目"
    )
    assumptions: list[str] = Field(default_factory=list)


class GuardVerdict(BaseModel):
    ok: bool
    rewritten_sql: str | None = None
    violations: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    doc_id: str
    title: str
    chunk_id: int
    snippet: str


class Answer(BaseModel):
    """最终返回给前端的统一结构，也是评测读的对象。

    intent / refused / cost_usd 这三个字段前端用不上，但 evals/run_eval.py 要读：
    没有它们，评测拿到的永远是 getattr 的默认值，分数恒为 0 —— 而且不报错。
    「被评测的对象要暴露评测需要的信息」，这是可测性的最低要求。
    """
    trace_id: str
    answer: str

    # --- 给评测用 ---
    intent: Intent | None = Field(
        default=None, description="路由判定的意图；evals 按它算 intent_acc"
    )
    refused: bool = Field(default=False, description="是否拒答；evals 按它算 refuse_acc")
    cost_usd: float = Field(default=0.0, description="这一次问答累计的 API 成本")
    numbers_from_sql: bool = Field(
        default=True, description="True 表示所有数字来自 SQL 执行结果，模型未参与算数"
    )
    sql: str | None = None
    rows: list[dict] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
