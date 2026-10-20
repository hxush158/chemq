"""跑一道题（默认混合题），把每次模型调用的输入和原始返回都打印出来。

用法（先 aws login --profile chemq，Titan 向量化要用）：
    cd ai && uv run python evals/trace_mixed.py "9月甲醇装置负荷率偏低是什么原因？"

只打补丁，不改业务代码：替换 Llm.complete / Llm.parse，打印后再调原函数。
  · Router、text2sql 走 parse（structured outputs）—— system 很长（语义层目录），只打印长度
  · kb_answer 走 complete —— 这就是"写文字"的那次调用，system 和 user 全部打印
"""
from __future__ import annotations

import json
import sys
from typing import Any

from chemq import llm as llm_mod
from chemq.pipeline import answer

LINE = "=" * 78
_n = 0


def _head(kind: str, model: str) -> None:
    global _n
    _n += 1
    print(f"\n{LINE}\n第 {_n} 次模型调用 · {kind} · {model}\n{LINE}")


def _system_len(system: Any) -> int:
    if isinstance(system, str):
        return len(system)
    return sum(len(b.get("text", "")) for b in system)


def _system_text(system: Any) -> str:
    return system if isinstance(system, str) else "\n".join(b.get("text", "") for b in system)


def _show_user(user: Any) -> None:
    if isinstance(user, str):
        print(user)
        return
    for i, b in enumerate(user):
        if b.get("type") == "search_result":
            body = "\n".join(t.get("text", "") for t in b.get("content", []))
            print(f"\n[search_result #{i}] title={b.get('title')}  source={b.get('source')}")
            print(body)
        else:
            print(f"\n[{b.get('type')} #{i}]")
            print(b.get("text", ""))


_orig_complete = llm_mod.Llm.complete
_orig_parse = llm_mod.Llm.parse


def traced_complete(self, **kw):
    _head("complete（自由文本 + 引用）", kw["model"])
    print("\n----- system（全文）-----")
    print(_system_text(kw["system"]))
    print("\n----- user（发给模型的内容块）-----")
    _show_user(kw["user"])
    res = _orig_complete(self, **kw)
    print("\n----- 模型原始返回 content（逐块）-----")
    for i, b in enumerate(res.content):
        d = b.model_dump() if hasattr(b, "model_dump") else dict(b)
        print(f"\n[block #{i}]")
        print(json.dumps(d, ensure_ascii=False, indent=2, default=str))
    print(f"\n----- usage: in={res.usage.input_tokens} out={res.usage.output_tokens} "
          f"cost=${res.usage.cost_usd:.4f} -----")
    return res


def traced_parse(self, **kw):
    _head(f"parse（structured outputs → {kw['schema'].__name__}）", kw["model"])
    print(f"\n----- system：{_system_len(kw['system'])} 字符（太长，不打印）-----")
    print("\n----- user -----")
    print(kw["user"])
    res = _orig_parse(self, **kw)
    print("\n----- 模型原始返回 parsed -----")
    p = res.parsed
    print(p.model_dump_json(indent=2) if hasattr(p, "model_dump_json") else p)
    print(f"\n----- usage: in={res.usage.input_tokens} out={res.usage.output_tokens} "
          f"cost=${res.usage.cost_usd:.4f} -----")
    return res


llm_mod.Llm.complete = traced_complete      # type: ignore[method-assign]
llm_mod.Llm.parse = traced_parse            # type: ignore[method-assign]


def main() -> None:
    q = sys.argv[1] if len(sys.argv) > 1 else "9月甲醇装置负荷率偏低是什么原因？"
    print(f"问题：{q}")
    a = answer(q)
    print(f"\n{LINE}\n最终返回给用户的 Answer\n{LINE}")
    print(a.model_dump_json(indent=2) if hasattr(a, "model_dump_json") else a)


if __name__ == "__main__":
    main()
