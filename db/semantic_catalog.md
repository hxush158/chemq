# 语义层目录

<!-- 自动生成：db/build_catalog.py（make catalog / make seed）。不要手改。 -->

数据覆盖：2025-10-01 ~ 2026-09-30（日粒度）。
只能查下面列出的视图和维度表（schema 为 chem）。没有列出的表和列都不存在，不要猜。

## 时间口径约定
- 基准月 = 基准时间所在的自然月，即使基准月还没过完。
- 「最近 N 个月 / 近 N 个月」= 含基准月在内往前数 N 个自然月（基准月 2026-09：近 3 个月 = 2026-07 ~ 2026-09，近 6 个月 = 2026-04 ~ 2026-09）。
- 「上个月」= 基准月的前一个月；「近一年 / 过去一年」= 含基准月的 12 个自然月。
- 「今年以来 / X 年以来」= 该年 1 月 1 日 ~ 数据覆盖终点；季度按自然季度（Q1 = 1~3 月）。

## 视图

### chem.v_plant_daily
装置日指标。口径：产量=合格品产量(不含返工)；开车率=实际运行小时/24；负荷率=产量/设计日产能
列：date_key（date），plant_id（text），plant_name（text），product_id（text），product_name（text），产量_t（numeric），总产出_t（numeric），一次合格率_pct（numeric），开车率_pct（numeric），负荷率_pct（numeric），非计划停车_次（integer）

### chem.v_energy_daily
能耗日指标。单位产品综合能耗 = Σ(能源量×折标系数) / 当日合格产量，单位 kgce/t，参照 GB/T 2589
列：date_key（date），plant_id（text），plant_name（text），综合能耗_kgce（numeric），能源成本_cny（numeric），产量_t（numeric），单位产品综合能耗_kgce_per_t（numeric）

### chem.v_sales_daily
销售日明细（一行一笔订单）。回款率=回款/不含税收入×100，按收付实现制；平均售价=不含税收入/销量，元/吨。汇总时先 sum 再相除，不要对回款率_pct 求平均
列：date_key（date），customer_id（text），customer_name（text），region（text），product_id（text），product_name（text），销量_t（numeric），不含税收入_cny（numeric），回款_cny（numeric），回款率_pct（numeric），平均售价_cny_per_t（numeric）

### chem.v_monthly_summary
月度经营汇总。各事实域先分别聚合到月粒度再关联，避免多对多扇出把指标放大
列：month_key（date），产量_t（numeric），平均开车率_pct（numeric），平均一次合格率_pct（numeric），非计划停车_次（bigint），单位产品综合能耗_kgce_per_t（numeric），不含税收入_cny（numeric），回款率_pct（numeric），平均售价_cny_per_t（numeric）

## 维度表

### chem.dim_date
日期维度。所有 fact 表按 date_key 关联，口径：自然日
列：date_key（date），year（integer），quarter（integer），month（integer），day_of_month（integer），is_workday（boolean）

### chem.dim_plant
装置维度。design_capacity_t_per_day 为设计日产能，用于负荷率计算
列：plant_id（text），plant_name（text），plant_type（text），design_capacity_t_per_day（numeric），commissioned_on（date）

### chem.dim_product
列：product_id（text），product_name（text），product_grade（text），uom（text），std_price_cny（numeric）

### chem.dim_customer
列：customer_id（text），customer_name（text），region（text），industry（text），credit_days（integer）

### chem.dim_energy_type
列：energy_id（text），energy_name（text），uom（text），coal_equiv_coef（numeric）

## 维度取值
按名称过滤时只能用这里的原值（一字不差），或者用对应的 ID；不要缩写、不要用 LIKE 模糊匹配。

### 装置（plant_id / plant_name）
- P01 · 合成氨装置 · 生产装置
- P02 · 硝酸装置 · 生产装置
- P03 · 甲醇装置 · 生产装置
- P04 · 双氧水装置 · 生产装置
- P05 · 公用工程 · 公用工程

### 产品（product_id / product_name）
- M01 · 液氨 · 优等品
- M02 · 硝酸 · 一等品
- M03 · 甲醇 · 优等品
- M04 · 双氧水 · 合格品

### 大区（region）
- 出口
- 华东
- 华中
- 华南

### 客户（customer_id / customer_name）
- C01 · 客户A-皖江农资 · 华东
- C02 · 客户B-长三角新材 · 华东
- C03 · 客户C-中原化肥 · 华中
- C04 · 客户D-岭南精化 · 华南
- C05 · 客户E-珠江贸易 · 华南
- C06 · 客户F-海外贸易 · 出口

### 能源（energy_id / energy_name）
- E01 · 电 · kWh
- E02 · 蒸汽 · t
- E03 · 天然气 · Nm3
- E04 · 新鲜水 · t
