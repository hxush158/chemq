"""提示词里的规则和示例：该有的要有，评测题原话不能出现（W2 的泄题教训）。"""
import json
from pathlib import Path

import pytest

from chemq import router
from chemq.text2sql import system_role

GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "golden.jsonl"


def _golden_questions() -> list[str]:
    return [json.loads(x)["question"] for x in GOLDEN.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.mark.parametrize("q", _golden_questions())
def test_router_examples_do_not_contain_golden_questions(q):
    assert q not in router.FEW_SHOT_EXAMPLES


def test_router_treats_described_metric_plus_why_as_mixed():
    """W3 Q020：数字已经写在问题里（"只有一半"），router 判成了 knowledge_lookup。"""
    assert "描述了某个指标的情况" in router.SYSTEM_ROLE
    assert "[示例 11]" in router.FEW_SHOT_EXAMPLES


def test_text2sql_leaves_the_why_to_knowledge_base():
    """W3 Q009 / Q020：SQL 为了解释原因加了按装置分组 / 逐日明细，行数对不上。"""
    r = system_role("postgres")
    assert "原因由知识库回答" in r and "不要为了解释原因额外加明细行" in r


def test_rule16_keeps_the_named_comparison_period():
    """rule 16 第一版写了"不要加对比维度"，Q009 就只返回了 3 月、把 2 月丢了 —— 规则矫枉过正。"""
    r = system_role("postgres")
    assert "点名的对比期" in r and "两期都要返回" in r
    assert "或对比维度" not in r


def test_holdout_questions_never_appear_in_any_prompt():
    """新题验证集的题目不能出现在任何提示词、示例、工具说明里，否则验证就失效了。"""
    from chemq import agents, tools
    from chemq.text2sql import system_role

    holdout = GOLDEN.parent / "holdout.jsonl"
    if not holdout.exists():
        pytest.skip("先 make golden 生成 holdout.jsonl")
    prompts = [router.SYSTEM_ROLE, router.FEW_SHOT_EXAMPLES, system_role("postgres"),
               agents.SYSTEM_PROMPT, tools.DESC_RUN_SQL]
    for line in holdout.read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = json.loads(line)["question"]
            assert not any(q in p for p in prompts), q
