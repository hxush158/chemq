"""在独立进程里验证领域启动时的 locale；不重载模块污染其他测试，不调 API。"""
import json
import os
import re
import subprocess
import sys

import pytest


@pytest.fixture(scope="module", params=[("chem", "postgres"), ("nz_power", "duckdb")])
def runtime(request):
    domain_id, dialect = request.param
    script = '''
import json
from types import SimpleNamespace as NS
from unittest.mock import patch

from chemq import agents, critic, kb_answer, pipeline, router, text2sql, tools
from chemq.domain import domain
from chemq.schemas import QueryPlan, SqlCandidate, TimeRange
from chemq.coverage import check_time
from datetime import date

d = domain()
question = "raw {question} {{as_of}}"
plan = QueryPlan(intent="out_of_scope", needs_knowledge=False, reasoning="unsupported")
result = NS(parsed=plan, usage=NS(cost_usd=0))
with patch.object(pipeline, "plan", return_value=result) as routed:
    pipeline.answer(question)
    router_user = routed.call_args.args[0]

fake = NS(parse=lambda **kw: kw)
router_call = router.plan(question, llm=fake)
# 本测试只管语言；语义层目录由另一个构建步骤生成，不作为这里的前置条件。
with patch.object(text2sql, "schema_context", return_value="# Test catalog"):
    sql_call = text2sql.generate(plan, llm=fake, question=question, as_of=d.as_of)
    tool_metrics = tools.list_metrics()

refusal = agents.to_answer("prefix " + agents.CANNOT_ANSWER + " reason",
                           agents.Recorder(), cost=0, stop="success")
llm = NS(complete=lambda **kw: NS(
    content=[NS(type="text", text="prefix " + kb_answer.NOT_COVERED + " reason", citations=[])],
    usage=NS(cost_usd=0)))
with patch.object(kb_answer, "hit_signals", return_value=["hit"]):
    kb = kb_answer.answer_from_kb(question,
        embedder=NS(embed=lambda q: NS(vector=[])), llm=llm, search=lambda *a: [])

claims = (["2026年3月单耗 134.75 kgce/t", "停了 3 次", "3万元", "7.18～7.44万吨"]
          if d.locale == "zh" else
          ["30 September 2026: HAY2201 price 134.75 NZD/MWh",
           "September 30, 2026: 3 hours", "Q3 2026: 5 percent",
           "2026-09-30: 7.18 to 7.44 million NZD", "rank 1", "3 times"])
scaled = "3万元" if d.locale == "zh" else "3 MILLION NZD"
scale_value = 30000 if d.locale == "zh" else 3000000
print(json.dumps({
    "locale": d.locale, "as_of": d.as_of, "question": question,
    "router_user": router_user, "sql_user": sql_call["user"],
    "sql_plan_only": text2sql.build_user_message(plan),
    "kb_user": kb_answer.build_user_content(question, [], [{"value": 12.5}]),
    "dictionary": router.metric_dictionary_block(),
    "system_dictionaries": [router_call["system"][-1]["text"], sql_call["system"][-1]["text"]],
    "tool_metrics": tool_metrics,
    "schemas": [QueryPlan.model_json_schema(), SqlCandidate.model_json_schema()],
    "agent_marker": agents.CANNOT_ANSWER, "kb_marker": kb_answer.NOT_COVERED,
    "refused": refusal.refused, "answer": refusal.answer,
    "kb_refused": kb.refused, "kb_kind": kb.kind, "kb_answer": kb.answer,
    "numbers": [critic.numbers_in_text(t) for t in claims],
    "scale_ok": critic.check(scaled, [{"value": scale_value}]).ok,
    "bad_scale_ok": critic.check(scaled, [{"value": scale_value / 10}]).ok,
    # 给用户看的时间拒答 / 裁剪理由（W3 第 0 步 ③）
    "time_refused": check_time(QueryPlan(intent="metric_query", needs_knowledge=False, reasoning="x",
        time_range=TimeRange(start="2025-01-01", end="2025-01-31")), (date(2026, 4, 1), date(2026, 9, 30))).reason,
    "time_clipped": check_time(QueryPlan(intent="metric_query", needs_knowledge=False, reasoning="x",
        time_range=TimeRange(start="2026-03-01", end="2026-04-30")), (date(2026, 4, 1), date(2026, 9, 30))).reason,
    "kb_texts": [kb_answer.NO_HIT_TEXT, kb_answer.NO_CITATION_TEXT],
}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "CHEMQ_DOMAIN": domain_id, "CHEMQ_SQL_DIALECT": dialect},
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_model_messages_and_schemas_follow_domain(runtime):
    r = runtime
    english = r["locale"] == "en"
    date_label, question_label = ("Base date: ", "User question: ") if english else ("基准时间：", "用户问题：")
    assert r["router_user"] == f'{date_label}{r["as_of"]}\n{question_label}{r["question"]}'
    assert r["question"] in r["sql_user"] and r["as_of"] in r["sql_user"]
    assert "time_range" in r["sql_user"]
    assert r["question"] not in r["sql_plan_only"] and r["as_of"] not in r["sql_plan_only"]
    assert r["kb_user"][-1]["text"] == question_label + r["question"]
    assert json.loads(r["kb_user"][0]["text"].split("\n", 1)[1]) == [{"value": 12.5}]
    title = "# Metric dictionary\n" if english else "# 指标口径字典\n"
    assert r["dictionary"].startswith(title)
    assert r["system_dictionaries"] == [r["dictionary"]] * 2
    assert r["tool_metrics"].startswith(r["dictionary"] + "\n\n")
    dimensions = r["schemas"][0]["properties"]["dimensions"]["description"]
    from chemq.domain import load_domain  # noqa: PLC0415

    slot = load_domain("nz_power" if english else "chem").config["prompt_slots"]["dimension_examples"]
    assert dimensions == (f"For example: {slot}" if english else f"如 {slot}")
    if english:
        for text in [r["sql_user"], r["kb_user"], r["schemas"], r["time_refused"], r["time_clipped"], r["kb_texts"]]:
            assert not re.search(r"[\u4e00-\u9fff]", json.dumps(text, ensure_ascii=False))


def test_refusals_are_recognised_in_current_locale(runtime):
    r = runtime
    assert r["refused"] and r["agent_marker"] not in r["answer"]
    assert r["kb_refused"] and r["kb_kind"] == "not_covered"
    assert r["kb_marker"] not in r["kb_answer"]


def test_critic_uses_current_locale(runtime):
    r = runtime
    expected = ([["134.75"], ["3"], ["3"], ["7.18", "7.44"]] if r["locale"] == "zh"
                else [["134.75"], ["3"], ["5"], ["7.18", "7.44"], [], ["3"]])
    assert r["numbers"] == expected
    assert r["scale_ok"] and not r["bad_scale_ok"]


def test_user_facing_time_text_follows_locale(runtime):
    """时间拒答、裁剪理由按语言取；中文和搬家之前一字不差（chem 输出不变）。"""
    r = runtime
    if r["locale"] == "zh":
        assert r["time_refused"] == "本期与数据覆盖范围 2026-04-01 至 2026-09-30 没有交集"
        assert r["time_clipped"] == "本期从 2026-03-01 至 2026-04-30 裁剪为 2026-04-01 至 2026-04-30，仅查询数据覆盖内的部分"
    else:
        assert "2026-04-01 to 2026-09-30" in r["time_refused"]
        assert "trimmed to 2026-04-01 to 2026-04-30" in r["time_clipped"]
