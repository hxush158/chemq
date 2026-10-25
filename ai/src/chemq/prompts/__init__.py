"""提示词模板：通用规则在引擎里（每种语言一套），领域相关的词留成空位，由领域包填。

    prompts/<locale>/<name>.md     长提示词（router_role、text2sql、agent、kb_answer）
    prompts/<locale>/strings.yaml  短句（工具说明、MCP 说明、示例的标签）

空位写成 {{name}}。值从两处来：领域包 domain.yaml 的 prompt_slots，加上调用方给的 extra
（方言相关的 ENGINE / MONTH_FMT 等，见 chemq/dialects.py）。

为什么是双花括号：提示词里本来就有单花括号 —— Router 示例里的 JSON（{"intent": ...}），
运行时才填的 {DATA_RANGE}、{as_of}。用 str.format 会把它们全当成空位然后报错。
这里只认 {{name}}，单花括号原样保留。

空位没给值 → 直接报错（KeyError），不会悄悄渲染出一个带 {{x}} 的提示词发给模型。
设计见 docs/11-domain-pack-design.md。
"""
from __future__ import annotations

import importlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..domain import domain

yaml = importlib.import_module("yaml")

PROMPTS_DIR = Path(__file__).resolve().parent
_SLOT = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def slot_names(text: str) -> set[str]:
    return set(_SLOT.findall(text))


def fill(text: str, slots: dict[str, Any], *, where: str) -> str:
    missing = sorted(slot_names(text) - set(slots))
    if missing:
        raise KeyError(
            f"{where} 用到的空位没有值：{missing}。领域相关的填在 domains/<领域>/domain.yaml 的 "
            f"prompt_slots 里，方言相关的在 chemq/dialects.py"
        )
    return _SLOT.sub(lambda m: str(slots[m.group(1)]), text)


def _slots(extra: dict[str, Any] | None) -> dict[str, Any]:
    slots = dict(domain().config.get("prompt_slots") or {})
    slots.update(extra or {})
    return slots


def _locale(locale: str | None) -> str:
    return locale or domain().locale


def template(name: str, locale: str | None = None) -> str:
    """模板原文（未填空位）。文件末尾的那一个换行是编辑器约定，不算提示词内容。"""
    loc = _locale(locale)
    path = PROMPTS_DIR / loc / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"没有 {loc} 版的提示词模板：{path}")
    text = path.read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


@lru_cache
def strings(locale: str) -> dict[str, str]:
    path = PROMPTS_DIR / locale / "strings.yaml"
    if not path.exists():
        raise FileNotFoundError(f"没有 {locale} 版的短句表：{path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def render(name: str, *, extra: dict[str, Any] | None = None, locale: str | None = None) -> str:
    loc = _locale(locale)
    return fill(template(name, loc), _slots(extra), where=f"prompts/{loc}/{name}.md")


def string(key: str, *, extra: dict[str, Any] | None = None, locale: str | None = None) -> str:
    loc = _locale(locale)
    table = strings(loc)
    if key not in table:
        raise KeyError(f"prompts/{loc}/strings.yaml 里没有 {key!r}")
    return fill(table[key], _slots(extra), where=f"prompts/{loc}/strings.yaml:{key}")
