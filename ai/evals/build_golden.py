#!/usr/bin/env python3
"""用参考 SQL 跑一遍数据库，把标准答案写进领域包的 eval/golden.jsonl 和 eval/holdout.jsonl。

题目在 eval/cases.yaml（每道题标 set: golden | holdout）。这个脚本只负责执行和生成，不含任何领域内容。

为什么不手写标准答案：手写会错，而且改了造数配置（data/seed.yaml）重新生成数据后就全失效。
这个脚本让"评测集"和"数据"永远同步 —— 数据变了，重跑一次就行。

用法: make golden（make seed 也会跑）。需要数据库已 seed。
"""
import json
import os
import shlex
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
# Makefile 用 --no-project 的裸 python 跑本脚本（CI 也是），import 不到 chemq.domain，
# 所以按同一个约定自己找：domains/<CHEMQ_DOMAIN>/。
DOMAIN_DIR = ROOT / "domains" / os.environ.get("CHEMQ_DOMAIN", "chem")
EVAL_DIR = DOMAIN_DIR / "eval"
CASES_FILE = EVAL_DIR / "cases.yaml"
OUT = EVAL_DIR / "golden.jsonl"
HOLDOUT = EVAL_DIR / "holdout.jsonl"
SETS = {"golden": OUT, "holdout": HOLDOUT}
SCHEMA = (yaml.safe_load((DOMAIN_DIR / "domain.yaml").read_text(encoding="utf-8")) or {}).get("schema", "public")


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


DSN = os.environ.get("CHEMQ_PG_DSN", "postgresql://postgres:chemq@localhost:5432/chemq")

# 默认用本机 psql；Makefile 会传 CHEMQ_PSQL="docker compose exec -T db psql"，
# 这样本机不装 PostgreSQL 客户端也能跑。
PSQL = shlex.split(os.environ.get("CHEMQ_PSQL", "psql"))


def _with_search_path(dsn: str) -> str:
    """把 search_path 写进连接串（libpq 的 options 参数），连接一建立就生效，不产生任何输出。

    为什么不用这两种看起来更简单的办法：
      · 环境变量 PGOPTIONS：Makefile 传进来的 PSQL 是 `docker compose exec -T db psql`，
        docker compose exec 不会把宿主机的环境变量带进容器，PGOPTIONS 在容器里不存在。
      · 在 SQL 前面拼 `SET search_path ...;`：psql 会把 SET 的命令标签也打印出来，
        结果每道题的标准答案里都多出一行 {"v": "SET"} —— 行数全错，exec_acc 会直接归零。
    连接串是作为参数传给 psql 的，进不进容器都一样有效。
    """
    opt = f"options=-csearch_path%3D{SCHEMA}%2Cpublic"
    if "options=" in dsn:
        return dsn
    return dsn + ("&" if "?" in dsn else "?") + opt


def run_sql(sql: str):
    """用 psql 跑参考 SQL。刻意不引入 psycopg —— 评测集构建脚本要能在最裸的环境里跑。"""
    proc = subprocess.run(
        [*PSQL, _with_search_path(DSN), "-tA", "-F", "\t", "-c", sql],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"SQL 执行失败: {proc.stderr.strip()}\n{sql}")
    rows = []
    for line in proc.stdout.strip().splitlines():
        parts = line.split("\t")
        rows.append({"k": parts[0], "v": parts[1]} if len(parts) == 2 else {"v": parts[0]})
    return rows


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
