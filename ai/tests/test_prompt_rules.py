"""提示词里的规则和示例：该有的要有，评测题原话不能出现（W2 的泄题教训）。"""
import json

import pytest

from chemq import router
from chemq.domain import domain
from chemq.text2sql import system_role

GOLDEN = domain().golden_path


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


def test_energy_cost_and_sales_volume_are_supported_metrics():
    """W4 新题验证：「能源成本」带"成本"两个字被一律拒答、「销量」不在字典里被 text2sql 拒答。"""
    import yaml

    from chemq.router import load_metric_dictionary

    d = yaml.safe_load(load_metric_dictionary())
    names = {m["name"] for m in d["metrics"]}
    assert {"能源成本", "销量"} <= names
    assert any("能源成本是支持的指标" in r for r in d["rules"])
    assert "不要因为名字里带\"成本\"两个字" in router.SYSTEM_ROLE


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


@pytest.mark.parametrize("domain_id", ["chem", "nz_power"])
def test_router_examples_cover_every_intent_and_never_copy_golden(domain_id):
    """11/15 NZ 基线：NZ 的示例里一道 mixed / knowledge_lookup 都没有，数值加解释题全被判成纯数值。
    每个领域的示例都要覆盖四种意图；上面那条泄题检查只看默认领域，这里每个领域都查。"""
    from chemq.domain import load_domain
    d = load_domain(domain_id)
    text = d.router_examples_path.read_text(encoding="utf-8")
    for intent in ("metric_query", "knowledge_lookup", "mixed", "out_of_scope"):
        assert f'"intent": "{intent}"' in text, intent
    golden = [json.loads(x)["question"] for x in d.golden_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    for q in golden:
        assert q not in text, q


def test_external_holdout_never_appears_in_prompts():
    """NZ 的 holdout 在仓库外，上面那条只查仓库里的文件，查不到它。make check-holdout 用环境变量把路径传进来。
    失败时只报行号、不打印题目原文：输出可以放心贴给别人看。"""
    import os
    from pathlib import Path

    from chemq import agents, kb_answer, tools
    from chemq.config import settings
    from chemq.text2sql import system_role

    path = os.environ.get("CHEMQ_HOLDOUT_FILE")
    if not path:
        pytest.skip("没有指定 CHEMQ_HOLDOUT_FILE（用 make check-holdout）")
    prompts = [router.SYSTEM_ROLE, router.FEW_SHOT_EXAMPLES, system_role(settings().sql_dialect),
               agents.SYSTEM_PROMPT, tools.DESC_RUN_SQL, kb_answer.SYSTEM_PROMPT]
    leaked = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            question = json.loads(line)["question"]
            if any(question in p for p in prompts):
                leaked.append(line_no)
    assert not leaked, f"holdout 第 {leaked} 行的题目出现在提示词里"
