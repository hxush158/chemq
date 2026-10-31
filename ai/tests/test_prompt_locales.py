"""各语言的提示词模板要对齐：同一套文件、同样的空位、同样的短句 key。以 zh 为基准。

为什么要测：改中文模板时加了一个 {{空位}} 或一条短句，忘了改英文版 →
英文领域包要到渲染时才报错；更糟的是英文版少了一段规则却完全不报错。
这里在单元测试阶段就拦下。只比"结构"（文件、空位、key），不比译文内容。
"""
from __future__ import annotations

import re

import pytest

from chemq import prompts

BASE = "zh"
LOCALES = sorted(
    p.name for p in prompts.PROMPTS_DIR.iterdir() if p.is_dir() and (p / "strings.yaml").exists()
)
OTHERS = [loc for loc in LOCALES if loc != BASE]
TEMPLATES = sorted(p.stem for p in (prompts.PROMPTS_DIR / BASE).glob("*.md"))

# 运行时用 str.format 填的单花括号（agent.md 里的 {as_of}、{data_range}）。
# 两边名字不一致 → format 时 KeyError。JSON 示例带引号（{"intent": ...}），不会被匹配到。
_SINGLE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")


def _files(loc: str) -> list[str]:
    return sorted(p.name for p in (prompts.PROMPTS_DIR / loc).iterdir() if p.is_file())


def test_there_is_a_second_locale():
    assert BASE in LOCALES
    assert OTHERS, "只有一种语言，这组测试什么都没比"


@pytest.mark.parametrize("loc", OTHERS)
def test_same_files(loc):
    assert _files(loc) == _files(BASE)


@pytest.mark.parametrize("loc", OTHERS)
def test_same_slots_per_template(loc):
    for name in TEMPLATES:
        base, other = prompts.template(name, BASE), prompts.template(name, loc)
        assert prompts.slot_names(other) == prompts.slot_names(base), f"{loc}/{name}.md 的 {{{{空位}}}} 和 {BASE} 不一致"
        assert set(_SINGLE.findall(other)) == set(_SINGLE.findall(base)), f"{loc}/{name}.md 的运行时 {{占位}} 和 {BASE} 不一致"
        markers = r"^\s*(\[\[if [a-z_][a-z0-9_]*\]\]|\[\[endif\]\])\s*$"
        assert re.findall(markers, other, re.MULTILINE) == re.findall(markers, base, re.MULTILINE)


@pytest.mark.parametrize("loc", LOCALES)
@pytest.mark.parametrize("has_kb", [False, True])
def test_agent_sections_render_in_each_locale(loc, has_kb):
    text = prompts.render("agent", locale=loc, sections={"kb": has_kb})
    assert ("search_knowledge" in text) == has_kb
    assert "[[" not in text
    assert re.findall(r"^(\d+)\.", text, re.MULTILINE) == (
        ["1", "2", "3", "4", "5", "6"] if has_kb else ["1", "2", "3", "4"]
    )


@pytest.mark.parametrize("loc", OTHERS)
def test_same_string_keys_and_slots(loc):
    base, other = prompts.strings(BASE), prompts.strings(loc)
    assert set(other) == set(base), {"缺": sorted(set(base) - set(other)), "多": sorted(set(other) - set(base))}
    for key in base:
        assert prompts.slot_names(other[key]) == prompts.slot_names(base[key]), f"{loc}/strings.yaml:{key}"


@pytest.mark.parametrize("loc", LOCALES)
@pytest.mark.parametrize("key, template", [
    ("marker_cannot_answer", "agent"),
    ("marker_not_covered", "kb_answer"),
])
def test_refusal_marker_matches_template(loc, key, template):
    marker = prompts.string(key, locale=loc)
    assert marker and marker in prompts.template(template, loc)
