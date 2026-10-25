"""W4：给 Agent 用的三个只读工具。只写这一份 —— MCP server（mcp_server.py）和
Agent SDK 的编排（agents.py）都调这里，保证两边的安全检查完全一样。

    list_metrics()            指标口径字典 + 语义层目录（写 SQL 之前先看这个）
    run_semantic_query(sql)   过 sqlguard 后，用只读账号执行
    search_knowledge(q)       混合检索 + 无命中判断

两条原则：
  1. **调用方是谁都要过 sqlguard。** MCP 的调用方是可以换的 —— 今天是 Claude Code，
     明天可能是别人写的 agent。护栏放在工具里，不放在"相信调用方"上。
  2. **region（行级权限）不是工具参数。** 它由服务端在建工具时绑定，模型改不了。
     如果把 region 做成参数，模型（或注入进问题里的指令）就能自己填"全国"。

工具的返回值都是可以直接 json.dumps 的 dict / str，出错也返回 dict（ok=False），
不抛异常 —— 让模型看到原因、自己改 SQL，比整个 agent 崩掉有用。
"""
from __future__ import annotations

import json
from typing import Any, Callable

from .config import settings
from .dialects import profile
from .prompts import string

# 工具说明只写这一份，MCP server 和 Agent SDK 都用它。
# 输出约定写在这里而不是某个调用方的提示词里：写在提示词里的规则，换个调用方就没了
# （W4：Agent 拿不到 text2sql 的 16 条规则，exec_acc 0.97 → 0.44，主要输在结果形状上）。
# 正文在 prompts/<locale>/strings.yaml；方言相关的（引擎名、月份写法）按当前引擎填。
_DIALECT = profile(settings().sql_dialect)
DESC_LIST_METRICS = string("tool_list_metrics")
DESC_RUN_SQL = string("tool_run_sql", extra=_DIALECT)
DESC_SEARCH = string("tool_search")

MAX_ROWS_TO_MODEL = 200          # 交给模型的行数上限；再多就是在烧 token，不是在分析
SNIPPET_CHARS = 600


def _jsonable(rows: list[dict]) -> list[dict]:
    """Decimal、date 这些 json 不认识的类型转成字符串。"""
    return json.loads(json.dumps(rows, ensure_ascii=False, default=str))


def list_metrics() -> str:
    from .router import load_metric_dictionary  # noqa: PLC0415
    from .text2sql import schema_context  # noqa: PLC0415

    return "# 指标口径字典\n" + load_metric_dictionary() + "\n\n" + schema_context()


def run_semantic_query(
    sql: str,
    *,
    region: str | None = None,
    run: Callable[..., list[dict]] | None = None,
) -> dict[str, Any]:
    """region 由调用方（服务端）绑定，不暴露给模型；run 只给测试注入用。"""
    from .sqlguard import check  # noqa: PLC0415

    s = settings()
    verdict = check(sql, s.allowed_relations, s.max_rows)
    if not verdict.ok or verdict.rewritten_sql is None:
        return {"ok": False, "stage": "guard", "violations": verdict.violations,
                "hint": "只能 SELECT 语义层目录里的视图和维度表；先调 list_metrics 看目录"}

    if run is None:
        from .db import run_query as run  # noqa: PLC0415
    try:
        rows = run(verdict.rewritten_sql, region=region)
    except Exception as e:  # noqa: BLE001 —— 语法错、列名错：把原因还给模型让它改
        return {"ok": False, "stage": "execute", "sql": verdict.rewritten_sql,
                "error": f"{type(e).__name__}: {e}"[:500]}

    return {
        "ok": True,
        "sql": verdict.rewritten_sql,
        "row_count": len(rows),
        "truncated": len(rows) > MAX_ROWS_TO_MODEL,
        "rows": _jsonable(rows[:MAX_ROWS_TO_MODEL]),
    }


def search_knowledge(
    question: str,
    *,
    embedder: Any = None,
    search: Callable[..., list] | None = None,
) -> dict[str, Any]:
    """found=False 时 hits 为空 —— 和第 6 步一样，检索层判无命中就不给模型任何资料。"""
    from .rag import hit_signals, hybrid_search  # noqa: PLC0415

    if embedder is None:
        from .embed import TitanEmbedder  # noqa: PLC0415
        embedder = TitanEmbedder()
    search = search or hybrid_search
    hits = search(question, embedder.embed(question).vector)
    signals = hit_signals(hits, question)
    if not signals:
        return {"found": False, "hits": [], "note": "知识库里没有和这个问题相关的资料"}
    return {
        "found": True,
        "signals": signals,
        "hits": [
            {
                "ref": f"{h.doc_id}#{h.chunk_id}",            # 回答里标出处用，见 agents.py 规则 3
                "doc_id": h.doc_id,
                "chunk_id": h.chunk_id,
                "source": h.content.split("\n", 1)[0],      # 「《标题》 › 章 › 条」
                "text": h.content.split("\n", 1)[-1][:SNIPPET_CHARS],
            }
            for h in hits
        ],
    }
