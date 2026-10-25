#!/usr/bin/env python3
"""从数据库生成领域包的「语义层目录」semantic_catalog.md 和数据覆盖区间 coverage.yaml。

text2sql 提示里给模型看的就是这份目录；Router / Agent 提示里的"数据覆盖"读 coverage.yaml。

为什么不再把 03_semantic_views.sql 原样塞进提示：
  视图定义里写着 `FROM fact_sales s`、`s.received_cny AS 回款_cny`。模型照着学，
  就会去查明细表、用英文底层列名（W3 主评测 Q004 真实发生过，被护栏拦下）。
  模型只需要知道"能查什么"，不需要知道"视图是怎么拼出来的"。

为什么从数据库生成而不是手写：手写的目录迟早和真实数据对不上（schema 漂移）；
造数配置改了名字，重跑 make seed 目录就跟着变。
生成结果提交进仓库：提示内容固定、可以审 diff、测试不用连数据库。

要查哪些视图、维度表、维度取值，写在领域包 domain.yaml 的 catalog 里；
目录里的标题、说明文字按领域包的语言取 chemq/prompts/<locale>/（strings.yaml、catalog_time_rules.md）。
这里只剩通用的生成逻辑。

用法: make catalog（make seed 最后也会自动跑）。在 ai/ 的 uv 环境里跑，要 import chemq。
"""
import os
import shlex
import subprocess

import yaml

from chemq.domain import domain
from chemq.prompts import render, string

DSN = os.environ.get("CHEMQ_PG_DSN", "postgresql://postgres:chemq@localhost:5432/chemq")
PSQL = shlex.split(os.environ.get("CHEMQ_PSQL", "psql"))
GENERATED = "<!-- 自动生成：scripts/build_catalog.py（make catalog / make seed）。不要手改。 -->"


def _with_search_path(dsn: str, schema: str) -> str:
    """同 ai/evals/build_golden.py：search_path 写进连接串，docker compose exec 也有效。"""
    opt = f"options=-csearch_path%3D{schema}%2Cpublic"
    if "options=" in dsn:
        return dsn
    return dsn + ("&" if "?" in dsn else "?") + opt


def q(sql: str) -> list[list[str]]:
    proc = subprocess.run([*PSQL, _with_search_path(DSN, domain().schema), "-tA", "-F", "\t", "-c", sql],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"SQL 执行失败: {proc.stderr.strip()}\n{sql}")
    return [line.split("\t") for line in proc.stdout.strip().splitlines() if line]


def _in(names: list[str]) -> str:
    return ",".join(f"'{n}'" for n in names)


def build() -> tuple[str, dict[str, str]]:
    d = domain()
    cat, schema = d.catalog, d.schema
    views, dims = list(cat["views"]), list(cat["dims"])
    names = views + dims
    cols: dict[str, list[tuple[str, str]]] = {n: [] for n in names}
    for t, c, typ in q(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        f"WHERE table_schema='{schema}' AND table_name IN ({_in(names)}) "
        "ORDER BY table_name, ordinal_position"
    ):
        cols[t].append((c, typ))
    comments = {r[0]: (r[1] if len(r) > 1 else "") for r in q(
        "SELECT c.relname, coalesce(obj_description(c.oid, 'pg_class'), '') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE n.nspname='{schema}' AND c.relname IN ({_in(names)})"
    )}
    dc = cat["date_column"]
    lo, hi = q(f"SELECT min({dc['column']}), max({dc['column']}) FROM {dc['relation']}")[0]

    missing = [n for n in names if not cols[n]]
    if missing:
        raise RuntimeError(f"数据库里找不到这些对象：{missing}。先 make seed")

    out = [
        string("catalog_title"),
        "",
        GENERATED,
        "",
        string("catalog_coverage", extra={"start": lo, "end": hi}),
        string("catalog_scope_note", extra={"schema": schema}),
        "",
        # 时间口径约定写在目录里，而不是只写在 router 的提示词里：
        # 目录是共享层，固定流程、Agent、Claude Code CLI 都看得到（W4：Agent 拿不到 router 的规则，
        # 把"近三个月"算成了不含当前月）。
        render("catalog_time_rules"),
        "",
        string("catalog_views"),
    ]
    col_sep, col_prefix = string("catalog_column_sep"), string("catalog_columns")
    for kind, group in (("views", views), ("dims", dims)):
        if kind == "dims":
            out += ["", string("catalog_dims")]
        for n in group:
            out += ["", f"### {schema}.{n}"]
            if comments.get(n):
                out.append(comments[n])
            out.append(col_prefix + col_sep.join(
                string("catalog_column", extra={"name": c, "type": typ}) for c, typ in cols[n]))

    out += ["", string("catalog_values"), string("catalog_values_note")]
    for v in cat["values"]:
        rows = q(v["sql"])
        out += ["", f"### {v['title']}"]
        out += ["- " + " · ".join(r) for r in rows]
    return "\n".join(out) + "\n", {"start": lo, "end": hi}


def _replace(path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")     # 先写临时文件再替换：失败时不毁掉上一次的结果
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main() -> None:
    d = domain()
    text, coverage = build()                          # 先在内存里生成完，再一次性替换文件
    _replace(d.catalog_path, text)
    _replace(d.coverage_path, "# " + GENERATED[5:-4] + "\n" + "# 数据覆盖区间。Router / Agent 的提示词用它（router.data_range()）。\n"
             + yaml.safe_dump(coverage, allow_unicode=True, sort_keys=False))
    print(f"✓ 语义层目录已生成：{d.catalog_path}（{len(text)} 字符）")
    print(f"✓ 数据覆盖区间：{coverage['start']} ~ {coverage['end']} -> {d.coverage_path}")


if __name__ == "__main__":
    main()
