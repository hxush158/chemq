"""命令行入口 —— 开发期最顺手的调试工具。

    chemq plan   "8月产量多少"     # 只看意图解析，最便宜
    chemq tokens "8月产量多少"     # 只数 token，不花钱 —— 调 prompt 长度时用它
    chemq ask    "8月产量多少"     # 走完整链路
    chemq sql    "8月各装置产量"    # 只看生成的 SQL，不执行
    chemq eval --week 1           # 跑评测（等价于仓库根目录的 make eval WEEK=1）

目录约定：
    make ...          在仓库根目录跑（Makefile 在那儿），或 make -C <根> ...
    uv run chemq ...  在 ai/ 目录跑（虚拟环境在 ai/.venv）
两边都能跑评测，选顺手的。
"""
from __future__ import annotations

import argparse
import json


def _usage_line(u) -> str:
    """把一次调用的账单摊开。四个数一起看才有意义：
       input        本次真正付全价的输入
       cache_write  写进缓存的（按 125% 计费）
       cache_read   命中缓存的（按 10% 计费）
       output       生成的
    """
    line = (
        f"\n[{u.model}]  {u.latency_ms}ms  ${u.cost_usd}\n"
        f"  input={u.input_tokens}  cache_write={u.cache_write_tokens}  "
        f"cache_read={u.cache_read_tokens}  output={u.output_tokens}"
    )
    hint = u.cache_hint()
    if hint:
        line += f"\n  ⚠ {hint}"
    return line


def main() -> int:
    ap = argparse.ArgumentParser(prog="chemq")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ask", "plan", "sql", "tokens"):
        s = sub.add_parser(name)
        s.add_argument("question")
        s.add_argument("--as-of", default=None, help="基准时间，默认取领域包 eval.as_of")
        s.add_argument("--scope", default=None)
    e = sub.add_parser("eval")
    e.add_argument("--week", type=int, default=1)
    args = ap.parse_args()

    if args.cmd == "eval":
        import subprocess  # noqa: PLC0415
        import sys  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        # cli.py -> chemq -> src -> ai，评测脚本在 ai/evals/ 下。
        # 之前写的 parents[3] 指到了仓库根，那里没有 evals/ —— 数层级要数清楚。
        script = Path(__file__).resolve().parents[2] / "evals" / "run_eval.py"
        # 用 sys.executable 而不是裸 "python"：保证跑的是当前虚拟环境的解释器
        return subprocess.run([sys.executable, str(script), "--week", str(args.week)]).returncode

    if args.cmd == "tokens":
        from .config import settings  # noqa: PLC0415
        from .llm import Llm, min_cache_tokens  # noqa: PLC0415
        from .router import SYSTEM_ROLE, data_range, load_metric_dictionary  # noqa: PLC0415

        llm = Llm()
        system = Llm.cached_system(
            SYSTEM_ROLE.replace("{DATA_RANGE}", data_range()),
            "# 指标口径字典\n" + load_metric_dictionary(),
        )
        print("各模型下，当前 router 前缀的缓存可行性：\n")
        print(f"  {'模型':<32}{'前缀 tokens':>12}{'缓存下限':>10}   结论")
        for model in (settings().model_router, "claude-sonnet-5", "claude-opus-5"):
            n = llm.count_tokens(model=model, system=system, user=args.question)
            need = min_cache_tokens(model)
            verdict = "✓ 可缓存" if need and n >= need else f"✗ 还差 {need - n}" if need else "?"
            print(f"  {model:<32}{n:>12}{need or '?':>10}   {verdict}")
        print("\n  count_tokens 接口不计费 —— 调 prompt 长度时先用它量，别靠猜。")
        return 0

    if args.cmd == "plan":
        from .router import plan  # noqa: PLC0415
        res = plan(args.question)
        print(res.parsed.model_dump_json(indent=2))
        print(_usage_line(res.usage))
        return 0

    from .pipeline import answer  # noqa: PLC0415
    a = answer(args.question, as_of=args.as_of, scope=args.scope)
    print(a.answer)
    if a.sql:
        print("\n--- SQL ---\n" + a.sql)
    if a.rows:
        print("\n--- 结果 ---\n" + json.dumps(a.rows[:20], ensure_ascii=False, indent=2))
    return 0
