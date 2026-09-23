#!/usr/bin/env python3
"""生成合成业务数据 -> db/02_seed.sql

所有业务命名来自 naming.yaml；换成曙光实际命名后重跑本脚本即可。
随机种子固定 —— 评测集的标准答案依赖数据可复现，不要改种子。

用法: python db/gen_seed.py > db/02_seed.sql
"""
import datetime as dt
import random
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "naming.yaml").read_text(encoding="utf-8"))

random.seed(20260914)

S = CFG["scenarios"]
START = dt.date.fromisoformat(S["date_range"]["start"])
END = dt.date.fromisoformat(S["date_range"]["end"])
OVERHAUL = S["overhaul"]
OH_START, OH_END = dt.date.fromisoformat(OVERHAUL["start"]), dt.date.fromisoformat(OVERHAUL["end"])
STOPS = {dt.date.fromisoformat(x["date"]): x["plant"] for x in S["unplanned_stops"]}
PD = S["price_decline"]
PD_FROM = dt.date.fromisoformat(PD["from"])
CA = S["cash_anomaly"]

PLANTS = CFG["plants"]
PRODUCTS = CFG["products"]
CUSTOMERS = CFG["customers"]
ENERGY = CFG["energy"]
PLANT_OF = {p["plant"]: p["id"] for p in PRODUCTS}      # plant_id -> product_id
ENERGY_SCALE = {"E01": 320000, "E02": 260, "E03": 42000, "E04": 1800}
PLANT_INTENSITY = {"P01": 1.00, "P02": 0.55, "P03": 0.85, "P04": 0.35}

out: list[str] = []
w = out.append


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


w("SET search_path = chem, public;")
w("BEGIN;")

d = START
while d <= END:
    w(f"INSERT INTO dim_date VALUES ('{d}',{d.year},{(d.month - 1) // 3 + 1},"
      f"{d.month},{d.day},{str(d.weekday() < 5).lower()});")
    d += dt.timedelta(days=1)

for p in PLANTS:
    cap = "NULL" if p["design_capacity"] is None else p["design_capacity"]
    w(f"INSERT INTO dim_plant VALUES ({q(p['id'])},{q(p['name'])},{q(p['type'])},{cap},'2019-06-01');")
for p in PRODUCTS:
    w(f"INSERT INTO dim_product VALUES ({q(p['id'])},{q(p['name'])},{q(p['grade'])},'t',{p['std_price']});")
for c in CUSTOMERS:
    w(f"INSERT INTO dim_customer VALUES ({q(c['id'])},{q(c['name'])},{q(c['region'])},"
      f"{q(c['industry'])},{c['credit_days']});")
for e in ENERGY:
    w(f"INSERT INTO dim_energy_type VALUES ({q(e['id'])},{q(e['name'])},{q(e['uom'])},{e['coal_equiv']});")

d = START
while d <= END:
    for p in PLANTS:
        cap = p["design_capacity"]
        if cap is None:
            continue
        pid = p["id"]
        run_h, stops = 24.0, 0
        if pid == OVERHAUL["plant"] and OH_START <= d <= OH_END:
            run_h = 0.0
        if STOPS.get(d) == pid:
            run_h, stops = round(random.uniform(9, 15), 2), 1

        load = random.uniform(0.86, 1.02) * (run_h / 24.0)
        output = round(cap * load, 3)
        qualified = round(output * random.uniform(0.965, 0.995), 3)
        w(f"INSERT INTO fact_production(date_key,plant_id,product_id,output_t,qualified_t,"
          f"rework_t,run_hours,unplanned_stops) VALUES ('{d}',{q(pid)},{q(PLANT_OF[pid])},"
          f"{output},{qualified},{round(output - qualified, 3)},{run_h},{stops});")

        # 停车期间固定能耗仍在 -> 单位能耗飙升，这是化工行业的真实现象
        factor = 0.25 + 0.75 * (run_h / 24.0)
        for e in ENERGY:
            qty = round(ENERGY_SCALE[e["id"]] * PLANT_INTENSITY[pid] * factor
                        * random.uniform(0.95, 1.05), 3)
            w(f"INSERT INTO fact_energy(date_key,plant_id,energy_id,qty,unit_price_cny)"
              f" VALUES ('{d}',{q(pid)},{q(e['id'])},{qty},{e['price']});")

    for p in PLANTS:
        lvl, desc = "NULL", "NULL"
        if random.random() < 0.004:
            lvl, desc = q("一般"), q("现场泄漏，已按应急预案处置")
        w(f"INSERT INTO fact_hse(date_key,plant_id,cod_mg_l,nh3n_mg_l,voc_mg_m3,"
          f"incident_level,incident_desc) VALUES ('{d}',{q(p['id'])},"
          f"{round(random.uniform(28, 68), 3)},{round(random.uniform(2, 14), 3)},"
          f"{round(random.uniform(8, 55), 3)},{lvl},{desc});")

    for c in CUSTOMERS:
        if random.random() < 0.55:
            continue
        prod = random.choice(PRODUCTS)
        qty = round(random.uniform(30, 260), 3)
        price = prod["std_price"]
        if prod["id"] == PD["product"] and d >= PD_FROM:
            price *= 1 - PD["pct_over_120d"] * ((d - PD_FROM).days / 120)
        price = round(price * random.uniform(0.97, 1.03), 2)
        rev = round(qty * price, 2)
        ratio = random.uniform(0.80, 1.0)
        if c["region"] == CA["region"] and d.year == CA["year"] and d.month == CA["month"]:
            ratio = random.uniform(*CA["ratio"])
        w(f"INSERT INTO fact_sales(date_key,customer_id,product_id,qty_t,revenue_cny,tax_cny,"
          f"received_cny,region) VALUES ('{d}',{q(c['id'])},{q(prod['id'])},{qty},{rev},"
          f"{round(rev * 0.13, 2)},{round(rev * ratio, 2)},{q(c['region'])});")

    if d.weekday() == 0:
        for prod in PRODUCTS:
            st = round(random.uniform(400, 2600), 3)
            w(f"INSERT INTO fact_inventory(date_key,product_id,stock_t,stock_value_cny)"
              f" VALUES ('{d}',{q(prod['id'])},{st},{round(st * prod['std_price'] * 0.82, 2)});")
    d += dt.timedelta(days=1)

cur = OH_START
while cur <= OH_END:
    w(f"INSERT INTO fact_maintenance(date_key,plant_id,mtn_type,down_hours,cost_cny)"
      f" VALUES ('{cur}',{q(OVERHAUL['plant'])},'计划检修',24,{round(random.uniform(40000, 120000), 2)});")
    cur += dt.timedelta(days=1)
for day, pid in STOPS.items():
    w(f"INSERT INTO fact_maintenance(date_key,plant_id,mtn_type,down_hours,cost_cny)"
      f" VALUES ('{day}',{q(pid)},'抢修',{round(random.uniform(9, 15), 2)},"
      f"{round(random.uniform(8000, 45000), 2)});")

w("COMMIT;")
print("\n".join(out))
