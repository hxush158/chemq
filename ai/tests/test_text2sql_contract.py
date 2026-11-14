"""text2sql / pipeline 的三条契约 —— 用假 Llm 测，不调 API、不连数据库。

  ① text2sql 能看到用户原话（「最低」「两个」这些词 QueryPlan 里没有）
  ② 答不了时有正规的拒答出口，而不是把拒绝写成 SQL 字面量
  ③ 成本包含生成 SQL 的那次调用，包括拒答和被护栏拦下的情况
"""
from typing import cast

import pytest

from chemq import pipeline
from chemq import text2sql as t2s
from chemq.llm import Llm, LlmResult, Usage
from chemq.schemas import Intent, QueryPlan, SqlCandidate, TimeRange
from chemq.sqlguard import check
from chemq.text2sql import SqlRun, Unanswerable, build_user_message, generate_and_run

ALLOWED = ("chem.v_plant_daily", "chem.v_sales_daily", "chem.dim_plant")

PLAN = QueryPlan(
    intent="metric_query",
    metrics=["一次合格率"],
    dimensions=["装置"],
    needs_knowledge=False,
    reasoning="问 9 月各装置一次合格率",
)

SONNET = Usage(model="claude-sonnet-5", input_tokens=2000, output_tokens=200)   # $0.006
HAIKU = Usage(model="claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=100)  # $0.0015


class FakeLlm:
    """只实现 generate() 用到的 parse()。记下每次调用的参数，供断言用。"""

    def __init__(self, candidate: SqlCandidate | None, usage: Usage = SONNET) -> None:
        self.candidate, self.usage, self.calls = candidate, usage, []

    def parse(self, **kw):
        self.calls.append(kw)
        return LlmResult(parsed=self.candidate, usage=self.usage)


# ================= ① 原话透传 =================

def test_user_message_carries_question_and_as_of():
    msg = build_user_message(PLAN, question="9月一次合格率最低的两个装置？", as_of="2026-09-14")
    assert "最低的两个" in msg          # 决定 ASC + LIMIT 2 的词，之前 text2sql 看不到
    assert "2026-09-14" in msg
    assert PLAN.model_dump_json() in msg
    assert "以它为准" in msg            # time_range 由 router 定，text2sql 不要再算一遍


def test_question_reaches_the_model(monkeypatch):
    monkeypatch.setattr(t2s, "run_query", lambda sql, scope=None: [{"v": 1}])
    fake = FakeLlm(SqlCandidate(sql="SELECT plant_name FROM chem.v_plant_daily"))
    generate_and_run(PLAN, question="9月一次合格率最低的两个装置？", as_of="2026-09-14", llm=cast(Llm, fake))
    assert "最低的两个" in fake.calls[0]["user"]


# ================= ② 拒答出口 =================

def test_model_refusal_raises_unanswerable_with_cost():
    fake = FakeLlm(SqlCandidate(refuse_reason="对比期 2025-01~09 无数据"))
    with pytest.raises(Unanswerable) as ei:
        generate_and_run(PLAN, llm=cast(Llm, fake))
    assert ei.value.kind == "model_refused"
    assert "对比期" in ei.value.reason
    assert ei.value.cost_usd == pytest.approx(SONNET.cost_usd)   # 拒答也花了钱


def test_literal_refusal_sql_is_caught_by_guard():
    """Q010 真实出现过的形态：模型把拒绝写成了一条字面量 SELECT。"""
    lit = "SELECT '查询被拒绝：数据覆盖区间为2025-10-01至2026-09-30' AS 提示"
    fake = FakeLlm(SqlCandidate(sql=lit))
    with pytest.raises(Unanswerable) as ei:
        generate_and_run(PLAN, llm=cast(Llm, fake))
    assert ei.value.kind == "guard_rejected"
    assert ei.value.sql == lit                                    # 原 SQL 留着，便于排查
    assert ei.value.cost_usd == pytest.approx(SONNET.cost_usd)


@pytest.mark.parametrize("sql", [
    "SELECT '查询被拒绝' AS 提示",
    "SELECT 1",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",     # 只引用了 CTE，CTE 里也没有表
])
def test_query_without_any_view_is_not_an_answer(sql):
    v = check(sql, ALLOWED)
    assert not v.ok
    assert any("没有引用任何语义层视图" in x for x in v.violations)


def test_normal_query_still_passes_guard():
    """新加的「至少引用一个视图」不能误伤正常查询，包括带 CTE 的。"""
    assert check("SELECT 产量_t FROM chem.v_plant_daily", ALLOWED).ok
    assert check("WITH x AS (SELECT * FROM chem.v_plant_daily) SELECT count(*) FROM x", ALLOWED).ok


def test_parse_failure_is_unanswerable_not_crash():
    with pytest.raises(Unanswerable) as ei:
        generate_and_run(PLAN, llm=cast(Llm, FakeLlm(None)))
    assert ei.value.kind == "parse_failed"


def test_unanswerable_is_still_a_permission_error():
    """兼容：之前护栏拦截抛的是 PermissionError，已有的 except 不该失效。"""
    assert issubclass(Unanswerable, PermissionError)


# ================= ③ 成本 =================

def test_success_path_carries_sql_cost(monkeypatch):
    monkeypatch.setattr(t2s, "run_query", lambda sql, scope=None: [{"v": 1}])
    run = generate_and_run(PLAN, llm=cast(Llm, FakeLlm(SqlCandidate(sql="SELECT 产量_t FROM chem.v_plant_daily"))))
    assert isinstance(run, SqlRun)
    assert run.rows == [{"v": 1}]
    assert "LIMIT 500" in run.sql.upper()
    assert run.cost_usd == pytest.approx(SONNET.cost_usd)


def _fake_plan(intent: Intent = "metric_query"):
    def f(_question):
        return LlmResult(
            parsed=QueryPlan(
                intent=intent, needs_knowledge=False, reasoning="r",
                time_range=TimeRange(start="2026-08-01", end="2026-08-31"),
            ), usage=HAIKU
        )
    return f


def test_pipeline_cost_is_router_plus_sql(monkeypatch):
    """之前 Answer.cost_usd 只有 router（Haiku）那一次，Sonnet 生成 SQL 的钱没算。"""
    monkeypatch.setattr(pipeline, "plan", _fake_plan())
    monkeypatch.setattr(
        pipeline, "generate_and_run",
        lambda *a, **k: SqlRun(sql="S", rows=[{"v": 1}], candidate=SqlCandidate(sql="S"), cost_usd=0.01),
    )
    a = pipeline.answer("8月总产量是多少？")
    assert a.refused is False
    assert a.cost_usd == pytest.approx(HAIKU.cost_usd + 0.01)


def test_pipeline_turns_unanswerable_into_refusal_with_cost(monkeypatch):
    """text2sql 拒答 → refused=True，进评测分母，而且成本照算。"""
    def boom(*a, **k):
        raise Unanswerable("对比期无数据", kind="model_refused", cost_usd=0.01)

    monkeypatch.setattr(pipeline, "plan", _fake_plan())
    monkeypatch.setattr(pipeline, "generate_and_run", boom)
    a = pipeline.answer("今年二季度合格率比去年二季度高多少？")
    assert a.refused is True
    assert a.answer == "对比期无数据"
    assert a.rows == []
    assert a.cost_usd == pytest.approx(HAIKU.cost_usd + 0.01)


def test_pipeline_passes_question_and_as_of(monkeypatch):
    seen = {}

    def spy(plan, **k):
        seen.update(k)
        return SqlRun(sql="S", rows=[], candidate=SqlCandidate(sql="S"), cost_usd=0.0)

    monkeypatch.setattr(pipeline, "plan", _fake_plan())
    monkeypatch.setattr(pipeline, "generate_and_run", spy)
    pipeline.answer("9月一次合格率最低的两个装置？", as_of="2026-09-14")
    assert seen["question"] == "9月一次合格率最低的两个装置？"
    assert seen["as_of"] == "2026-09-14"


# ================= ④ 执行出错修一次（11/15 NZ 基线 N020） =================

class ProgrammingError(Exception):
    """模拟驱动抛的 SQL 错误：按类名认，和 DuckDB / psycopg / Snowflake 一样。"""


class OperationalError(Exception):
    """模拟连不上、超时这类环境错误。"""


class SeqLlm:
    """按顺序返回几个候选，记下每次调用。"""

    def __init__(self, *candidates: SqlCandidate) -> None:
        self.candidates, self.calls = list(candidates), []

    def parse(self, **kw):
        self.calls.append(kw)
        return LlmResult(parsed=self.candidates[len(self.calls) - 1], usage=SONNET)


BAD = SqlCandidate(sql="SELECT 不存在的列 FROM chem.v_plant_daily")
GOOD = SqlCandidate(sql="SELECT 产量_t FROM chem.v_plant_daily")


def _run_query_failing(times: int, exc_type=ProgrammingError):
    calls = []

    def run(sql, scope=None):
        calls.append(sql)
        if len(calls) <= times:
            raise exc_type('Binder Error: column "不存在的列" not found')
        return [{"v": 1}]
    return run, calls


def test_sql_error_is_repaired_once_with_the_error_message(monkeypatch):
    run, calls = _run_query_failing(1)
    monkeypatch.setattr(t2s, "run_query", run)
    fake = SeqLlm(BAD, GOOD)
    result = generate_and_run(PLAN, llm=cast(Llm, fake))
    assert result.rows == [{"v": 1}] and "产量_t" in result.sql
    assert result.cost_usd == pytest.approx(SONNET.cost_usd * 2)       # 两次调用都要记成本
    repair_msg = fake.calls[1]["user"]
    assert "不存在的列" in repair_msg and "Binder Error" in repair_msg   # 出错的 SQL 和报错原文都交给了模型
    assert fake.calls[1]["system"] == fake.calls[0]["system"]           # system 不变，缓存照样命中


def test_sql_error_twice_becomes_refusal_not_crash(monkeypatch):
    run, _ = _run_query_failing(2)
    monkeypatch.setattr(t2s, "run_query", run)
    with pytest.raises(Unanswerable) as e:
        generate_and_run(PLAN, llm=cast(Llm, SeqLlm(BAD, BAD)))
    assert e.value.kind == "sql_error"
    assert e.value.cost_usd == pytest.approx(SONNET.cost_usd * 2)


def test_environment_error_is_not_sent_to_the_model(monkeypatch):
    run, _ = _run_query_failing(1, OperationalError)
    monkeypatch.setattr(t2s, "run_query", run)
    fake = SeqLlm(GOOD, GOOD)
    with pytest.raises(OperationalError):
        generate_and_run(PLAN, llm=cast(Llm, fake))
    assert len(fake.calls) == 1                                          # 连不上不是 SQL 的错，不让模型改


def test_repaired_sql_still_goes_through_the_guard(monkeypatch):
    run, _ = _run_query_failing(1)
    monkeypatch.setattr(t2s, "run_query", run)
    with pytest.raises(Unanswerable) as e:
        generate_and_run(PLAN, llm=cast(Llm, SeqLlm(BAD, SqlCandidate(sql="SELECT '查不到'"))))
    assert e.value.kind == "guard_rejected"
