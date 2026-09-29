#!/usr/bin/env python3
"""正则护栏 vs AST 护栏 —— 同样的输入，两种判断方式，看差别。

    cd ai && uv run python ../scripts/ast_vs_regex.py

W2 破坏实验的配套演示。跑完你会明白为什么 sqlguard.py 用 sqlglot 而不是 re。
"""
import re
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai" / "src"))
from chemq.sqlguard import check  # noqa: E402

ALLOWED = (
    "chem.v_plant_daily", "chem.v_energy_daily",
    "chem.v_sales_daily", "chem.v_monthly_summary",
)

# 一个"看起来挺严"的正则护栏 —— 很多项目里就是这么写的
DANGER = re.compile(r"\b(drop|delete|update|insert|alter|truncate)\b", re.I)


def regex_guard(sql: str) -> tuple[bool, str]:
    m = DANGER.search(sql)
    return (False, f"命中危险词 {m.group()}") if m else (True, "放行")


CASES = [
    ("正常查询",
     "SELECT date_key, 产量_t FROM chem.v_plant_daily WHERE plant_id = 'P01'"),
    ("正常 CTE",
     "WITH x AS (SELECT * FROM chem.v_plant_daily) SELECT count(*) FROM x"),
    ("分号多语句 + 注释",
     "SELECT 1; /*harmless*/ DROP TABLE chem.fact_sales"),
    ("UNION 越表（无危险词！）",
     "SELECT * FROM chem.v_plant_daily UNION SELECT * FROM chem.fact_sales"),
    ("CTE 里藏 DELETE",
     "WITH d AS (DELETE FROM chem.fact_sales RETURNING *) SELECT * FROM d"),
    ("直查明细表（无危险词！）",
     "SELECT * FROM chem.fact_sales"),
    ("探测数据库结构（无危险词！）",
     "SELECT tablename FROM pg_catalog.pg_tables"),
    ("拖库：没有 LIMIT",
     "SELECT * FROM chem.v_sales_daily"),
    ("注释里带危险词（正则误杀）",
     "SELECT 产量_t FROM chem.v_plant_daily -- 别 drop 这张表"),
]

print("\n" + "=" * 78)
print(f"  {'场景':<26}{'正则':<20}{'sqlguard (AST)'}")
print("=" * 78)
for name, sql in CASES:
    r_ok, r_why = regex_guard(sql)
    v = check(sql, ALLOWED, max_rows=500)
    r = ("放行" if r_ok else "拦截") + ("" if r_ok else f"·{r_why[:8]}")
    a = "放行" if v.ok else "拦截"
    # 两边判断不一致的地方，才是重点
    mark = "  " if r_ok == v.ok else "←←"
    print(f"{mark}{name:<26}{r:<20}{a}")
print("=" * 78)

print("\n\n不一致的几条，展开看 AST 看到了什么：\n")
for name, sql in CASES:
    r_ok, _ = regex_guard(sql)
    v = check(sql, ALLOWED, max_rows=500)
    if r_ok == v.ok:
        continue
    print("─" * 78)
    print(f"  {name}")
    print(f"  SQL: {sql}")
    stmts = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    print(f"  ① 解析出 {len(stmts)} 条语句")
    for i, s in enumerate(stmts):
        print(f"  ② [{i}] 根节点类型 = {type(s).__name__}")
        tables = [t.sql() for t in s.find_all(exp.Table)]
        print(f"  ③ [{i}] 树里涉及的表 = {tables}")
        writes = [type(n).__name__ for n in
                  s.find_all(exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter)]
        print(f"  ④ [{i}] 树里的写操作节点 = {writes or '无'}")
    print(f"  → sqlguard 判定: {'放行' if v.ok else '拦截'}")
    if v.violations:
        for x in v.violations:
            print(f"      · {x}")
    print()

print("─" * 78)
print("\n完整 AST 长什么样（正常查询）：\n")
tree = sqlglot.parse_one(CASES[0][1], read="postgres")
print(repr(tree))

print("\n\nLIMIT 改写演示：\n")
for sql in ["SELECT * FROM chem.v_sales_daily",
            "SELECT * FROM chem.v_sales_daily LIMIT 100000",
            "SELECT * FROM chem.v_sales_daily LIMIT 10"]:
    v = check(sql, ALLOWED, max_rows=500)
    print(f"  输入: {sql}")
    print(f"  执行: {v.rewritten_sql}\n")
