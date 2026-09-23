-- ============================================================
-- ChemQ · 示例化工经营分析智能问数 · 业务库 Schema
-- PostgreSQL 16 + pgvector
-- 注意：本 schema 为训练用示例，装置/产品命名请按示例化工实际情况替换。
-- 设计原则：星型模型 + 语义层视图 + 只读角色 + 行级权限(RLS)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DROP SCHEMA IF EXISTS chem CASCADE;
CREATE SCHEMA chem;
SET search_path = chem, public;

-- ---------- 维度 ----------
CREATE TABLE dim_date (
    date_key        date PRIMARY KEY,
    year            int  NOT NULL,
    quarter         int  NOT NULL,
    month           int  NOT NULL,
    day_of_month    int  NOT NULL,
    is_workday      boolean NOT NULL DEFAULT true
);
COMMENT ON TABLE dim_date IS '日期维度。所有 fact 表按 date_key 关联，口径：自然日';

CREATE TABLE dim_plant (
    plant_id        text PRIMARY KEY,
    plant_name      text NOT NULL,          -- 装置/车间名
    plant_type      text NOT NULL,          -- 生产装置 / 公用工程 / 罐区
    design_capacity_t_per_day numeric(12,2),-- 设计日产能(吨)
    commissioned_on date
);
COMMENT ON TABLE dim_plant IS '装置维度。design_capacity_t_per_day 为设计日产能，用于负荷率计算';

CREATE TABLE dim_product (
    product_id      text PRIMARY KEY,
    product_name    text NOT NULL,
    product_grade   text,                    -- 优等品/一等品/合格品
    uom             text NOT NULL DEFAULT 't',
    std_price_cny   numeric(12,2)            -- 标准售价(不含税, 元/吨)
);

CREATE TABLE dim_customer (
    customer_id     text PRIMARY KEY,
    customer_name   text NOT NULL,
    region          text NOT NULL,           -- 华东/华中/华南/出口
    industry        text,
    credit_days     int NOT NULL DEFAULT 30
);

CREATE TABLE dim_energy_type (
    energy_id       text PRIMARY KEY,
    energy_name     text NOT NULL,           -- 电/蒸汽/天然气/新鲜水
    uom             text NOT NULL,           -- kWh / t / Nm3 / t
    coal_equiv_coef numeric(10,4) NOT NULL   -- 折标煤系数 kgce/uom (GB/T 2589)
);
COMMENT ON COLUMN dim_energy_type.coal_equiv_coef IS '折标煤系数，综合能耗计算的唯一口径来源，禁止在 SQL 中硬编码';

-- ---------- 事实 ----------
CREATE TABLE fact_production (
    id              bigserial PRIMARY KEY,
    date_key        date NOT NULL REFERENCES dim_date(date_key),
    plant_id        text NOT NULL REFERENCES dim_plant(plant_id),
    product_id      text NOT NULL REFERENCES dim_product(product_id),
    output_t        numeric(14,3) NOT NULL,   -- 总产出(吨)
    qualified_t     numeric(14,3) NOT NULL,   -- 合格品产量(吨)  ← "产量"默认口径
    rework_t        numeric(14,3) NOT NULL DEFAULT 0,
    run_hours       numeric(6,2)  NOT NULL,   -- 装置当日实际运行小时
    unplanned_stops int NOT NULL DEFAULT 0    -- 非计划停车次数
);
COMMENT ON TABLE fact_production IS '生产日报。"产量"口径 = qualified_t（合格品），不含 rework_t';

CREATE TABLE fact_energy (
    id              bigserial PRIMARY KEY,
    date_key        date NOT NULL REFERENCES dim_date(date_key),
    plant_id        text NOT NULL REFERENCES dim_plant(plant_id),
    energy_id       text NOT NULL REFERENCES dim_energy_type(energy_id),
    qty             numeric(16,3) NOT NULL,   -- 原始计量值(按 dim_energy_type.uom)
    unit_price_cny  numeric(12,4) NOT NULL
);

CREATE TABLE fact_sales (
    id              bigserial PRIMARY KEY,
    date_key        date NOT NULL REFERENCES dim_date(date_key),
    customer_id     text NOT NULL REFERENCES dim_customer(customer_id),
    product_id      text NOT NULL REFERENCES dim_product(product_id),
    qty_t           numeric(14,3) NOT NULL,
    revenue_cny     numeric(16,2) NOT NULL,   -- 不含税收入
    tax_cny         numeric(16,2) NOT NULL DEFAULT 0,
    received_cny    numeric(16,2) NOT NULL DEFAULT 0,  -- 已回款
    region          text NOT NULL             -- 冗余，用于行级权限过滤
);
COMMENT ON COLUMN fact_sales.revenue_cny IS '不含税收入。口径：开票确认收入，退货冲回当期';

CREATE TABLE fact_inventory (
    id              bigserial PRIMARY KEY,
    date_key        date NOT NULL REFERENCES dim_date(date_key),
    product_id      text NOT NULL REFERENCES dim_product(product_id),
    stock_t         numeric(14,3) NOT NULL,   -- 期末库存
    stock_value_cny numeric(16,2) NOT NULL
);

CREATE TABLE fact_hse (
    id              bigserial PRIMARY KEY,
    date_key        date NOT NULL REFERENCES dim_date(date_key),
    plant_id        text NOT NULL REFERENCES dim_plant(plant_id),
    cod_mg_l        numeric(10,3),            -- 废水 COD
    nh3n_mg_l       numeric(10,3),            -- 氨氮
    voc_mg_m3       numeric(10,3),            -- VOCs
    incident_level  text,                     -- null/一般/较大/重大
    incident_desc   text
);

CREATE TABLE fact_maintenance (
    id              bigserial PRIMARY KEY,
    date_key        date NOT NULL REFERENCES dim_date(date_key),
    plant_id        text NOT NULL REFERENCES dim_plant(plant_id),
    mtn_type        text NOT NULL,            -- 计划检修/抢修
    down_hours      numeric(6,2) NOT NULL,
    cost_cny        numeric(14,2) NOT NULL DEFAULT 0
);

-- ---------- 知识库(非结构化) ----------
CREATE TABLE kb_document (
    doc_id          text PRIMARY KEY,
    title           text NOT NULL,
    doc_type        text NOT NULL,            -- 制度/口径字典/月度经营分析/操作规程
    effective_from  date,
    source_uri      text                      -- s3://...
);

CREATE TABLE kb_chunk (
    chunk_id        bigserial PRIMARY KEY,
    doc_id          text NOT NULL REFERENCES kb_document(doc_id) ON DELETE CASCADE,
    ord             int  NOT NULL,
    content         text NOT NULL,
    token_count     int,
    embedding       vector(1024),             -- 见 docs/03-rag.md 选型说明
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
);
CREATE INDEX kb_chunk_tsv_idx  ON kb_chunk USING gin(tsv);
CREATE INDEX kb_chunk_vec_idx  ON kb_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX kb_chunk_trgm_idx ON kb_chunk USING gin (content gin_trgm_ops);

-- ---------- 审计(智能问数自身的埋点) ----------
CREATE TABLE qa_audit (
    trace_id        uuid PRIMARY KEY,
    asked_at        timestamptz NOT NULL DEFAULT now(),
    user_id         text NOT NULL,
    user_role       text NOT NULL,
    question        text NOT NULL,
    intent          text,
    generated_sql   text,
    sql_rejected_reason text,
    row_count       int,
    citations       jsonb,
    answer          text,
    model           text,
    input_tokens    int,
    output_tokens   int,
    cache_read_tokens int,
    cost_usd        numeric(10,6),
    latency_ms      int,
    human_verdict   text            -- 人工复核：正确/口径错/数据错/拒答正确
);
COMMENT ON TABLE qa_audit IS '每一次问数的完整链路留痕，是 W6 评测与可观测的数据源';
