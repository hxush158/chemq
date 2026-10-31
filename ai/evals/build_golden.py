#!/usr/bin/env python3
"""用参考 SQL 跑一遍数据库，把标准答案写进领域包的 eval/golden.jsonl 和 eval/holdout.jsonl。

题目在 eval/cases.yaml（每道题标 set: golden | holdout）。这个脚本只负责执行和生成，不含任何领域内容。

为什么不手写标准答案：手写会错，而且改了造数配置（data/seed.yaml）重新生成数据后就全失效。
这个脚本让"评测集"和"数据"永远同步 —— 数据变了，重跑一次就行。

用法: make golden（make seed 也会跑）。
在 ai/ 的 uv 环境中运行，需要 db 可选依赖及已准备好的领域数据库。
"""
import json
import os
from pathlib import Path

import yaml

from chemq.db import engine
from chemq.domain import domain
from chemq.result_text import golden_row

# 路径来自当前领域包；这里只读配置，不连接数据库。
EVAL_DIR = domain().eval_dir
CASES_FILE = EVAL_DIR / "cases.yaml"
OUT = domain().golden_path
HOLDOUT = domain().holdout_path
SETS = {"golden": OUT, "holdout": HOLDOUT}


def load_cases(path: Path = CASES_FILE) -> dict[str, list[dict]]:
    """读 cases.yaml，按 set 分组。set 只用来分组，不写进 jsonl；其余字段原样、按原顺序保留。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    groups: dict[str, list[dict]] = {k: [] for k in SETS}
    seen: set[str] = set()
    for c in raw:
        cid = c.get("id")
        if not cid:
            raise ValueError(f"{path}：有一道题没有 id：{c}")
        if cid in seen:
            raise ValueError(f"{path}：id 重复：{cid}")
        seen.add(cid)
        c = dict(c)
        s = c.pop("set", None)
        if s not in SETS:
            raise ValueError(f"{path}：{cid} 的 set={s!r}，只能是 {sorted(SETS)}")
        groups[s].append(c)
    return groups


def run_sql(sql: str) -> list[dict[str, str]]:
    """通过当前领域包的引擎执行参考 SQL，保持标准答案的文本格式。"""
    return [golden_row(row) for row in engine().run_query(sql)]


def _build(cases: list[dict]) -> list[str]:
    lines = []
    for c in cases:
        rec = dict(c)
        rec.setdefault("assert_type", "sql")
        sql = rec.pop("sql", None)
        if sql:
            rec["reference_sql"] = " ".join(sql.split())
            rec["expected"] = run_sql(sql)
        lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
    return lines


def _write(path: Path, lines: list[str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp, path)         # 同一文件系统内是原子操作：要么旧文件，要么完整的新文件


def main() -> None:
    # 先在内存里把全部标准答案算完，再一次性替换文件。
    # 原来是先 open("w") 再逐条跑 SQL —— 第一条 SQL 一失败，旧的评测集已经被清空了。
    # 生成结果的脚本，失败时至少不能毁掉上一次的结果。
    groups = load_cases()
    built = {name: _build(cases) for name, cases in groups.items()}
    for name, path in SETS.items():
        _write(path, built[name])
        print(f"wrote {len(groups[name])} cases -> {path}")


if __name__ == "__main__":
    main()
