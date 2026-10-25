#!/usr/bin/env python3
"""从数据库生成「语义层目录」domains/<领域>/semantic_catalog.md —— text2sql 提示里给模型看的就是它。

为什么不再把 03_semantic_views.sql 原样塞进提示：
  视图定义里写着 `FROM fact_sales s`、`s.received_cny AS 回款_cny`。模型照着学，
  就会去查明细表、用英文底层列名（W3 主评测 Q004 真实发生过，被护栏拦下）。
  模型只需要知道"能查什么"，不需要知道"视图是怎么拼出来的"。

为什么从数据库生成而不是手写：手写的目录迟早和真实数据对不上（schema 漂移）；
造数配置（data/seed.yaml）改了名字，重跑 make seed 目录就跟着变。
生成结果提交进仓库：提示内容固定、可以审 diff、测试不用连数据库。

用法: make catalog（make seed 最后也会自动跑）
"""
import os
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 同 ai/evals/build_golden.py：裸 python 跑，import 不到 chemq.domain，按约定自己算路径。
# ⚠ W1 第 1 步只搬家：下面的 VIEWS / DIMS / VALUES 仍是化工专用，第 2 步搬进 domain.yaml。
OUT = ROOT / "domains" / os.environ.get("CHEMQ_DOMAIN", "chem") / "semantic_catalog.md"

DSN = os.environ.get("CHEMQ_PG_DSN", "postgresql://postgres:chemq@localhost:5432/chemq")
PSQL = shlex.split(os.environ.get("CHEMQ_PSQL", "psql"))

VIEWS = ["v_plant_daily", "v_energy_daily", "v_sales_daily", "v_monthly_summary"]
DIMS = ["dim_date", "dim_plant", "dim_product", "dim_customer", "dim_energy_type"]

# 维度取值：(标题, SQL)。按名称过滤时模型只能用这里的原值。
VALUES = [
    ("装置（plant_id / plant_name）",
     "SELECT plant_id, plant_name, plant_type FROM dim_plant ORDER BY 1"),
    ("产品（product_id / product_name）",
     "SELECT product_id, product_name, product_grade FROM dim_product ORDER BY 1"),
    ("大区（region）",
     "SELECT DISTINCT region FROM v_sales_daily ORDER BY 1"),
    ("客户（customer_id / customer_name）",
     "SELECT customer_id, customer_name, region FROM dim_customer ORDER BY 1"),
    ("能源（energy_id / energy_name）",
     "SELECT energy_id, energy_name, uom FROM dim_energy_type ORDER BY 1"),
]


def _with_search_path(dsn: str) -> str:
    """同 ai/evals/build_golden.py：search_path 写进连接串，docker compose exec 也有效。"""
    opt = "options=-csearch_path%3Dchem%2Cpublic"
    if "options=" in dsn:
        return dsn
    return dsn + ("&" if "?" in dsn else "?") + opt


def q(sql: str) -> list[list[str]]:
    proc = subprocess.run([*PSQL, _with_search_path(DSN), "-tA", "-F", "\t", "-c", sql],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"SQL 执行失败: {proc.stderr.strip()}\n{sql}")
    return [line.split("\t") for line in proc.stdout.strip().splitlines() if line]


def _in(names: list[str]) -> str:
    return ",".join(f"'{n}'" for n in names)


def build() -> str:
    names = VIEWS + DIMS
    cols: dict[str, list[tuple[str, str]]] = {n: [] for n in names}
    for t, c, typ in q(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        f"WHERE table_schema='chem' AND table_name IN ({_in(names)}) "
        "ORDER BY table_name, ordinal_position"
    ):
        cols[t].append((c, typ))
    comments = {r[0]: (r[1] if len(r) > 1 else "") for r in q(
        "SELECT c.relname, coalesce(obj_description(c.oid, 'pg_class'), '') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE n.nspname='chem' AND c.relname IN ({_in(names)})"
    )}
    lo, hi = q("SELECT min(date_key), max(date_key) FROM v_plant_daily")[0]

    missing = [n for n in names if not cols[n]]
    if missing:
        raise RuntimeError(f"数据库里找不到这些对象：{missing}。先 make seed")

    out = [
        "# 语义层目录",
        "",
        "<!-- 自动生成：scripts/build_catalog.py（make catalog / make seed）。不要手改。 -->",
        "",
        f"数据覆盖：{lo} ~ {hi}（日粒度）。",
        "只能查下面列出的视图和维度表（schema 为 chem）。没有列出的表和列都不存在，不要猜。",
        "",
        # 时间口径约定写在目录里，而不是只写在 router 的提示词里：
        # 目录是共享层，固定流程、Agent、Claude Code CLI 都看得到（W4：Agent 拿不到 router 的规则，
        # 把"近三个月"算成了不含当前月）。
        "## 时间口径约定",
        "- 基准月 = 基准时间所在的自然月，即使基准月还没过完。",
        "- 「最近 N 个月 / 近 N 个月」= 含基准月在内往前数 N 个自然月"
        "（基准月 2026-09：近 3 个月 = 2026-07 ~ 2026-09，近 6 个月 = 2026-04 ~ 2026-09）。",
        "- 「上个月」= 基准月的前一个月；「近一年 / 过去一年」= 含基准月的 12 个自然月。",
        "- 「今年以来 / X 年以来」= 该年 1 月 1 日 ~ 数据覆盖终点；季度按自然季度（Q1 = 1~3 月）。",
        "",
        "## 视图",
    ]
    for kind, group in (("视图", VIEWS), ("维度表", DIMS)):
        if kind == "维度表":
            out += ["", "## 维度表"]
        for n in group:
            out += ["", f"### chem.{n}"]
            if comments.get(n):
                out.append(comments[n])
            out.append("列：" + "，".join(f"{c}（{typ}）" for c, typ in cols[n]))

    out += ["", "## 维度取值",
            "按名称过滤时只能用这里的原值（一字不差），或者用对应的 ID；不要缩写、不要用 LIKE 模糊匹配。"]
    for title, sql in VALUES:
        rows = q(sql)
        out += ["", f"### {title}"]
        out += ["- " + " · ".join(r) for r in rows]
    return "\n".join(out) + "\n"


def main() -> None:
    text = build()                       # 先在内存里生成完，再一次性替换文件
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, OUT)
    print(f"✓ 语义层目录已生成：{OUT.relative_to(ROOT)}（{len(text)} 字符）")


if __name__ == "__main__":
    main()
