"""提示词模板机制（chemq/prompts）：空位怎么填、漏了会怎样、领域词有没有漏进通用模板。"""
from __future__ import annotations

import pytest

from chemq import prompts
from chemq.dialects import DIALECT_PROFILE
from chemq.domain import domain

LOCALE = "zh"
TEMPLATES = sorted(p.stem for p in (prompts.PROMPTS_DIR / LOCALE).glob("*.md"))
DIALECT_KEYS = {k for prof in DIALECT_PROFILE.values() for k in prof}


def _all_template_texts() -> dict[str, str]:
    out = {f"{n}.md": prompts.template(n, LOCALE) for n in TEMPLATES}
    out.update({f"strings.yaml:{k}": v for k, v in prompts.strings(LOCALE).items()})
    return out


def test_every_slot_in_templates_has_a_value():
    """模板里每个 {{空位}}，要么领域包给了值，要么是方言表里的键，要么是调用方现填的。"""
    known = set(domain().config.get("prompt_slots") or {}) | DIALECT_KEYS | prompts.RUNTIME_SLOTS
    for where, text in _all_template_texts().items():
        assert prompts.slot_names(text) <= known, (where, prompts.slot_names(text) - known)


def test_every_domain_slot_is_used():
    """domain.yaml 里写了却没有模板用到的空位 = 改了它也不生效，迟早有人白改。"""
    used = set().union(*(prompts.slot_names(t) for t in _all_template_texts().values()))
    unused = set(domain().config.get("prompt_slots") or {}) - used
    assert not unused, f"prompt_slots 里这些没有模板用到：{sorted(unused)}"


def test_slot_values_are_not_hardcoded_in_templates():
    """通用模板里不该出现领域词：某个空位的值原样出现在模板里，说明有人绕过空位写死了。"""
    slots = domain().config.get("prompt_slots") or {}
    for where, text in _all_template_texts().items():
        for name, value in slots.items():
            if "\n" in value:
                continue
            assert value not in text, f"{where} 里写死了 {value!r}（应该用 {{{{{name}}}}}）"


def test_missing_slot_fails_loudly():
    with pytest.raises(KeyError, match="nope"):
        prompts.fill("你好 {{nope}}", {}, where="test")


def test_single_braces_are_left_alone():
    """单花括号是运行时才填的（{DATA_RANGE}、{as_of}）或示例里的 JSON，模板机制不碰。"""
    text = '基准 {as_of}，示例 {"intent": "mixed"}，公司 {{x}}'
    assert prompts.fill(text, {"x": "A"}, where="test") == '基准 {as_of}，示例 {"intent": "mixed"}，公司 A'


def test_router_example_numbers_follow_file_order():
    from chemq import router

    n = len(__import__("yaml").safe_load(domain().router_examples_path.read_text(encoding="utf-8")))
    assert f"[示例 {n}]" in router.FEW_SHOT_EXAMPLES
    assert f"[示例 {n + 1}]" not in router.FEW_SHOT_EXAMPLES
