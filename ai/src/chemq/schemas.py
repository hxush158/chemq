"""所有跨层数据结构。

刻意用 pydantic 定义，因为它们同时是 Claude structured outputs 的 JSON Schema 来源：
    client.messages.parse(..., output_format=QueryPlan)
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .prompts import string

Intent = Literal["metric_query", "knowledge_lookup", "mixed", "chitchat", "out_of_scope"]


class TimeRange(BaseModel):
    start: str = Field(description=string("schema_time_start"))
    end: str = Field(description=string("schema_time_end"))
    grain: Literal["day", "month", "quarter", "year"] = "month"


class QueryPlan(BaseModel):
    """W1 产出：把中文问题结构化。不含 SQL —— 意图和取数分两步，便于单独评测。"""
    model_config = ConfigDict(json_schema_extra={"description": string("schema_query_plan")})

    intent: Intent
    metrics: list[str] = Field(default_factory=list, description=string("schema_metrics"))
    dimensions: list[str] = Field(default_factory=list, description=string("schema_dimensions"))
    filters: dict[str, str] = Field(default_factory=dict)
    time_range: TimeRange | None = None
    compare_range: TimeRange | None = Field(
        default=None,
        description=string("schema_compare_range"),
    )

    needs_knowledge: bool = Field(description=string("schema_needs_knowledge"))
    reasoning: str = Field(description=string("schema_reasoning"))


class SqlCandidate(BaseModel):
    """W2 产出。

    refuse_reason 是 text2sql 这一层的「拒答出口」。

    没有它的时候，模型判断出"用语义层答不了"，唯一能做的就是把拒绝写成一条
    SQL 字面量：SELECT '查询被拒绝：……' AS 提示。这条 SQL 能过护栏（只读、
    不碰表）、能执行、返回一行，于是被当成了答案，refused=False。Q010 真实发生过。
    schema 里表达不了的东西，模型只能塞进它能表达的地方。
    """
    model_config = ConfigDict(json_schema_extra={"description": string("schema_sql_candidate")})

    sql: str = Field(
        default="",
        description=string("schema_sql"),
    )
    refuse_reason: str | None = Field(
        default=None,
        description=string("schema_refuse_reason"),
    )
    metric_definitions_used: list[str] = Field(
        default_factory=list, description=string("schema_metric_definitions_used")
    )
    assumptions: list[str] = Field(default_factory=list, description=string("schema_assumptions"))


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
        default=None, description=string("schema_answer_intent")
    )
    refused: bool = Field(default=False, description=string("schema_answer_refused"))
    cost_usd: float = Field(default=0.0, description=string("schema_answer_cost"))
    numbers_from_sql: bool = Field(
        default=True, description=string("schema_answer_numbers_from_sql")
    )
    sql: str | None = None
    rows: list[dict] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
