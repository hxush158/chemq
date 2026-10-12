-- 登录账号。应用连 chemq_app（可写审计表），取数连 chemq_ro_login（继承只读 role）
-- 生产环境密码走 AWS Secrets Manager，不要进代码库。
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='chemq_app') THEN
    CREATE ROLE chemq_app LOGIN PASSWORD 'chemq';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='chemq_ro_login') THEN
    CREATE ROLE chemq_ro_login LOGIN PASSWORD 'chemq' IN ROLE chemq_ro;
  END IF;
END $$;

GRANT USAGE ON SCHEMA chem TO chemq_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA chem TO chemq_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA chem TO chemq_app;
-- W3：ingest 每次「先删后插」，所以知识库两张表要额外给 DELETE（业务表仍然不给）
GRANT DELETE ON chem.kb_document, chem.kb_chunk TO chemq_app;
-- chemq_ro_login 的权限完全来自 chemq_ro（见 03_semantic_views.sql），此处不额外授权
