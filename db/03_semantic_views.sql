-- ============================================================
-- 语义层：把"指标口径"固化成视图，模型只允许查询这些视图
-- 这是智能问数准确率的第一杠杆 —— 不要让模型自己拼口径
-- ============================================================
SET search_path = chem, public;

-- 日粒度装置生产指标
CREATE OR REPLACE VIEW v_plant_daily AS
SELECT
    p.date_key,
    p.plant_id,
    pl.plant_name,
    p.product_id,
    pr.product_name,
    p.qualified_t                                   AS 产量_t,
    p.output_t                                      AS 总产出_t,
    CASE WHEN p.output_t > 0
         THEN round(p.qualified_t / p.output_t * 100, 2) END AS 一次合格率_pct,
    round(p.run_hours / 24.0 * 100, 2)              AS 开车率_pct,
    CASE WHEN pl.design_capacity_t_per_day > 0
         THEN round(p.qualified_t / pl.design_capacity_t_per_day * 100, 2) END AS 负荷率_pct,
    p.unplanned_stops                               AS 非计划停车_次
FROM fact_production p
JOIN dim_plant   pl ON pl.plant_id   = p.plant_id
JOIN dim_product pr ON pr.product_id = p.product_id;

COMMENT ON VIEW v_plant_daily IS
'装置日指标。口径：产量=合格品产量(不含返工)；开车率=实际运行小时/24；负荷率=产量/设计日产能';

-- 综合能耗（折标煤），口径来源唯一：dim_energy_type.coal_equiv_coef
CREATE OR REPLACE VIEW v_energy_daily AS
WITH e AS (
    SELECT f.date_key, f.plant_id,
           sum(f.qty * et.coal_equiv_coef)      AS kgce,
           sum(f.qty * f.unit_price_cny)        AS energy_cost_cny
    FROM fact_energy f
    JOIN dim_energy_type et ON et.energy_id = f.energy_id
    GROUP BY 1,2
),
q AS (
    SELECT date_key, plant_id, sum(qualified_t) AS qualified_t
    FROM fact_production GROUP BY 1,2
)
SELECT e.date_key, e.plant_id, pl.plant_name,
       round(e.kgce, 2)                                       AS 综合能耗_kgce,
       round(e.energy_cost_cny, 2)                            AS 能源成本_cny,
       q.qualified_t                                          AS 产量_t,
       CASE WHEN q.qualified_t > 0
            THEN round(e.kgce / q.qualified_t, 2) END         AS 单位产品综合能耗_kgce_per_t
FROM e
JOIN q  ON q.date_key = e.date_key AND q.plant_id = e.plant_id
JOIN dim_plant pl ON pl.plant_id = e.plant_id;

COMMENT ON VIEW v_energy_daily IS
'能耗日指标。单位产品综合能耗 = Σ(能源量×折标系数) / 当日合格产量，单位 kgce/t，参照 GB/T 2589';

-- 销售与回款
CREATE OR REPLACE VIEW v_sales_daily AS
SELECT s.date_key, s.customer_id, c.customer_name, s.region,
       s.product_id, pr.product_name,
       s.qty_t                                 AS 销量_t,
       s.revenue_cny                           AS 不含税收入_cny,
       s.received_cny                          AS 回款_cny,
       CASE WHEN s.revenue_cny > 0
            THEN round(s.received_cny / s.revenue_cny * 100, 2) END AS 回款率_pct,
       CASE WHEN s.qty_t > 0
            THEN round(s.revenue_cny / s.qty_t, 2) END               AS 平均售价_cny_per_t
FROM fact_sales s
JOIN dim_customer c ON c.customer_id = s.customer_id
JOIN dim_product pr ON pr.product_id = s.product_id;

-- 月度经营汇总（给"上月经营情况怎么样"这类问题一个确定的落点）
--
-- 【扇出陷阱】这个视图第一版是直接把三张日表 JOIN 在一起的，结果产量被放大了 2.5 倍：
--   v_plant_daily 一天有 4 行（4 个装置），v_sales_daily 一天有 N 行（N 笔订单），
--   按 date_key 关联后变成 4×N 行，sum(产量_t) 自然虚高。
-- 正确做法：每个事实域先各自聚合到同一粒度，再按粒度 JOIN。
-- 这是企业 Text-to-SQL 最高频的错误来源之一 —— 模型写出来的 SQL 语法全对，数字全错，
-- 而且错得"看起来很合理"。把它固化进视图，模型就没机会犯这个错。
CREATE OR REPLACE VIEW v_monthly_summary AS
WITH m_prod AS (
    SELECT date_trunc('month', date_key)::date AS month_key,
           sum(产量_t)                          AS 产量_t,
           round(avg(开车率_pct), 2)            AS 平均开车率_pct,
           round(avg(一次合格率_pct), 2)        AS 平均一次合格率_pct,
           sum(非计划停车_次)                   AS 非计划停车_次
    FROM v_plant_daily GROUP BY 1
),
m_energy AS (
    SELECT date_trunc('month', date_key)::date AS month_key,
           sum(综合能耗_kgce)                   AS 综合能耗_kgce,
           sum(能源成本_cny)                    AS 能源成本_cny
    FROM v_energy_daily GROUP BY 1
),
m_sales AS (
    SELECT date_trunc('month', date_key)::date AS month_key,
           sum(不含税收入_cny)                  AS 不含税收入_cny,
           sum(回款_cny)                        AS 回款_cny,
           sum(销量_t)                          AS 销量_t
    FROM v_sales_daily GROUP BY 1
)
SELECT p.month_key,
       p.产量_t,
       p.平均开车率_pct,
       p.平均一次合格率_pct,
       p.非计划停车_次,
       round(e.综合能耗_kgce / NULLIF(p.产量_t, 0), 2)          AS 单位产品综合能耗_kgce_per_t,
       s.不含税收入_cny,
       round(s.回款_cny / NULLIF(s.不含税收入_cny, 0) * 100, 2) AS 回款率_pct,
       round(s.不含税收入_cny / NULLIF(s.销量_t, 0), 2)         AS 平均售价_cny_per_t
FROM m_prod p
LEFT JOIN m_energy e USING (month_key)
LEFT JOIN m_sales  s USING (month_key);

COMMENT ON VIEW v_monthly_summary IS
'月度经营汇总。各事实域先分别聚合到月粒度再关联，避免多对多扇出把指标放大';

-- ---------- 只读角色 + 行级权限 ----------
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='chemq_ro') THEN
        CREATE ROLE chemq_ro NOLOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA chem TO chemq_ro;
GRANT SELECT ON v_plant_daily, v_energy_daily, v_sales_daily, v_monthly_summary TO chemq_ro;
GRANT SELECT ON dim_date, dim_plant, dim_product, dim_customer, dim_energy_type TO chemq_ro;
-- 刻意不授予 fact_* 明细表：模型只能走语义层
REVOKE ALL ON fact_sales FROM chemq_ro;

-- 行级权限示例：大区销售只能看本大区
ALTER TABLE fact_sales ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_sales_region ON fact_sales;
CREATE POLICY rls_sales_region ON fact_sales
    FOR SELECT TO chemq_ro
    USING (
        current_setting('chemq.region', true) IS NULL
        OR current_setting('chemq.region', true) = 'ALL'
        OR region = current_setting('chemq.region', true)
    );
-- 用法：每次查询前 SET LOCAL chemq.region = '华东';
