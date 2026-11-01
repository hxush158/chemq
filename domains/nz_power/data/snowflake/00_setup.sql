-- NZ Power on Snowflake：一次性初始化。W2 第 0 步，设计见 docs/10-flagship-plan.md 的 W2 一节。
--
-- 谁来跑：账号管理员（注册试用账号的那个人，ACCOUNTADMIN），在 Snowsight 的 worksheet 里整段执行。
-- 跑之前：
--   1. 按 docs/10 W2 一节的命令生成两对密钥（loader、reporter 各一对），私钥加密放在 ~/.snowflake/
--   2. 把下面两个占位 <LOADER_PUBLIC_KEY> / <REPORTER_PUBLIC_KEY> 换成对应 .pub 文件的内容
--      （去掉 -----BEGIN/END PUBLIC KEY----- 两行，剩下的拼成一行）
--   ⚠ 只在 Snowsight 里替换，不要把替换后的文件提交（公钥不是机密，但这份文件是模板，保持占位符）
--
-- 防线（和 DuckDB 不同，Snowflake 有真正的角色权限）：
--   第一道：CHEMQ_REPORTER 只有 3 张表的 SELECT（导数脚本导完逐张授权，见第 6 节说明）
--   第二道：sqlguard 白名单 + Snowflake 敏感写法拦截
--   兜底：语句超时（用户级 + warehouse 级，两者取较小值）、资源监控（额度用完自动挂起）
--
-- 可重复执行：全部用 IF NOT EXISTS。改了参数要用 ALTER，不会覆盖已有对象。

USE ROLE ACCOUNTADMIN;

-- ---------- 1. 资源监控：防止"忘了关、跑一晚上"把试用额度烧光 ----------
CREATE RESOURCE MONITOR IF NOT EXISTS CHEMQ_RM
  WITH CREDIT_QUOTA = 10
       FREQUENCY = MONTHLY
       START_TIMESTAMP = IMMEDIATELY
  TRIGGERS ON 80 PERCENT DO NOTIFY
           ON 100 PERCENT DO SUSPEND_IMMEDIATE;

-- ---------- 2. warehouse：最小规格，空闲 60 秒自动挂起（官方提醒：不要关掉自动挂起） ----------
CREATE WAREHOUSE IF NOT EXISTS CHEMQ_WH
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  STATEMENT_TIMEOUT_IN_SECONDS = 300      -- 任何语句的硬上限；导数的 COPY 也受它约束
  RESOURCE_MONITOR = CHEMQ_RM
  COMMENT = 'ChemQ NZ power: loader + reporter';

-- ---------- 3. 库和 schema（名字和 domains/nz_power/domain.yaml 的 schema: main 对应） ----------
CREATE DATABASE IF NOT EXISTS NZ_POWER COMMENT = 'ChemQ NZ power serving copy';
CREATE SCHEMA IF NOT EXISTS NZ_POWER.MAIN;

-- ---------- 4. 角色：导数和查询分开 ----------
CREATE ROLE IF NOT EXISTS CHEMQ_LOADER   COMMENT = 'create + load the 3 serving tables';
CREATE ROLE IF NOT EXISTS CHEMQ_REPORTER COMMENT = 'read-only: SELECT on the 3 serving tables';
-- 挂到 SYSADMIN 下：官方推荐的角色层级，管理员能看到、管理这两个角色建的对象
GRANT ROLE CHEMQ_LOADER   TO ROLE SYSADMIN;
GRANT ROLE CHEMQ_REPORTER TO ROLE SYSADMIN;

GRANT USAGE ON WAREHOUSE CHEMQ_WH          TO ROLE CHEMQ_LOADER;
GRANT USAGE ON DATABASE  NZ_POWER          TO ROLE CHEMQ_LOADER;
GRANT USAGE ON SCHEMA    NZ_POWER.MAIN     TO ROLE CHEMQ_LOADER;
GRANT CREATE TABLE ON SCHEMA NZ_POWER.MAIN TO ROLE CHEMQ_LOADER;

GRANT USAGE ON WAREHOUSE CHEMQ_WH          TO ROLE CHEMQ_REPORTER;
GRANT USAGE ON DATABASE  NZ_POWER          TO ROLE CHEMQ_REPORTER;
GRANT USAGE ON SCHEMA    NZ_POWER.MAIN     TO ROLE CHEMQ_REPORTER;
-- 不用 GRANT SELECT ON FUTURE TABLES：那样 loader 以后建的任何表（包括临时表）reporter 都能读。
-- 改为导数脚本导完之后，按 domain.yaml 的 catalog.views 逐张 GRANT SELECT（表的 owner 是 CHEMQ_LOADER，可以授权）。

-- ---------- 5. 两个程序用户（TYPE=SERVICE：不能用密码登录，只能密钥对） ----------
CREATE USER IF NOT EXISTS CHEMQ_LOADER_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = CHEMQ_LOADER
  DEFAULT_WAREHOUSE = CHEMQ_WH
  DEFAULT_NAMESPACE = NZ_POWER.MAIN
  RSA_PUBLIC_KEY = '<LOADER_PUBLIC_KEY>'
  COMMENT = 'ChemQ: make snowflake-load only';
GRANT ROLE CHEMQ_LOADER TO USER CHEMQ_LOADER_SVC;

CREATE USER IF NOT EXISTS CHEMQ_REPORTER_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = CHEMQ_REPORTER
  DEFAULT_WAREHOUSE = CHEMQ_WH
  DEFAULT_NAMESPACE = NZ_POWER.MAIN
  RSA_PUBLIC_KEY = '<REPORTER_PUBLIC_KEY>'
  STATEMENT_TIMEOUT_IN_SECONDS = 30       -- 查询账号再收紧一层（和 warehouse 的 300 取较小值）
  COMMENT = 'ChemQ: SnowflakeEngine (read-only)';
GRANT ROLE CHEMQ_REPORTER TO USER CHEMQ_REPORTER_SVC;

-- ---------- 6. 检查（第 0 步验收的前半段；后半段是用 reporter 的私钥连上来，CREATE TABLE 被拒） ----------
DESC USER CHEMQ_LOADER_SVC;     -- RSA_PUBLIC_KEY_FP 有值、TYPE = SERVICE
DESC USER CHEMQ_REPORTER_SVC;
SHOW GRANTS TO ROLE CHEMQ_REPORTER;   -- 此时只有 warehouse / database / schema 的 USAGE，导数后再多 3 条 SELECT

-- ---------- 收尾（试用结束前录完屏后，或者怀疑私钥泄露时执行；平时不要跑） ----------
-- DROP USER IF EXISTS CHEMQ_REPORTER_SVC;
-- DROP USER IF EXISTS CHEMQ_LOADER_SVC;
-- DROP DATABASE IF EXISTS NZ_POWER;
-- DROP WAREHOUSE IF EXISTS CHEMQ_WH;
-- DROP RESOURCE MONITOR IF EXISTS CHEMQ_RM;
-- DROP ROLE IF EXISTS CHEMQ_REPORTER;
-- DROP ROLE IF EXISTS CHEMQ_LOADER;
-- 只换钥匙：ALTER USER CHEMQ_REPORTER_SVC SET RSA_PUBLIC_KEY_2 = '<新公钥>'; 确认新钥匙能连后 UNSET RSA_PUBLIC_KEY
