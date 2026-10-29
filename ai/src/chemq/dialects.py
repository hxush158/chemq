"""方言表：同一条规则在不同 SQL 引擎上的写法。属于引擎层，和领域包无关。

提示词模板里用 {{ENGINE}}、{{ENGINE_NAME}}、{{MONTH_FMT}} 引用这里的值（见 chemq/prompts）。
W1 第 4 步在这里加 duckdb，W2 加 snowflake：每加一种方言，两张表（DIALECT_PROFILE、
SENSITIVE_TOKENS_BY_DIALECT）都要加一条。
"""
from __future__ import annotations

# 抽成一张方言表，而不是维护几份 prompt —— 几份 prompt 一定会漂移。
# W2 那几条输出契约当初选择写进 prompt 而不是写进 evals 的 rows_match 归一化逻辑，
# 回报就在这里：它们现在是可按方言替换的参数，而不是评测代码里的 if dialect == ...
DIALECT_PROFILE: dict[str, dict[str, str]] = {
    "postgres": {
        "ENGINE": "PostgreSQL 16",                       # text2sql：「你是 … 专家」
        "ENGINE_NAME": "PostgreSQL",                     # 工具说明：「执行一条只读 SQL（…）」
        "MONTH_FMT": "to_char(<日期列>, 'YYYY-MM')",
    },
    # ⚠️ 接真实 Doris 时要验证：date_format 的格式串是 MySQL 系的 %Y-%m，
    #    to_char 在 Doris 上大概率不存在。验证前不要当成已确认的事实。
    "doris": {
        "ENGINE": "Apache Doris 4.0",
        "ENGINE_NAME": "Apache Doris",
        "MONTH_FMT": "date_format(<日期列>, '%Y-%m')",
    },
    "duckdb": {
        "ENGINE": "DuckDB",
        "ENGINE_NAME": "DuckDB",
        "MONTH_FMT": "strftime(<日期列>, '%Y-%m')",
    },
}


def profile(dialect: str) -> dict[str, str]:
    try:
        return DIALECT_PROFILE[dialect]
    except KeyError:
        raise ValueError(f"没有 {dialect!r} 的方言配置，可选：{sorted(DIALECT_PROFILE)}") from None


# 敏感 token 是 AST 白名单之上的「腰带」，不是「裤子」—— 结构性攻击由 AST 拦，
# 这里只补那些「语法完全合法、但在这个引擎上能读到不该读的东西」的函数与语句。
# 它是按引擎走的：pg_read_file 在 Doris 上不存在，INTO OUTFILE 在 Postgres 上不存在。
_TOKENS_COMMON = ("information_schema", "set role")

_TOKENS_POSTGRES = (
    "pg_catalog", "pg_sleep", "pg_read_file",
    "pg_ls_dir", "lo_import", "dblink", "copy ", "\\copy",
)

# ⚠️ 这一组是按 Doris 的能力推的，W5 接真实 Doris 时必须逐条验证：
#   into outfile / export  —— Doris 导出到 S3/HDFS/本地，是最直接的拖库通道
#   s3( / hdfs( / local( / jdbc(  —— 表值函数，能绕过白名单直接读外部数据源
#   __internal_schema      —— Doris 内部库
#   sleep(                 —— MySQL 系的耗时函数，打满 FE 连接
_TOKENS_DORIS = (
    "into outfile", "outfile", "export ", "__internal_schema",
    "sleep(", "s3(", "hdfs(", "local(", "jdbc(",
)

# DuckDB：读文件的表值函数（read_* / glob / parquet_scan）、挂别的库（attach）、
# 装扩展（install / load）、导入导出（copy / export / import database）、读环境（getenv / duckdb_settings）。
# 引擎连接时已关掉外部访问，这里是 sqlguard 这一层的拦截，两层都要有。
_TOKENS_DUCKDB = (
    "read_csv", "read_csv_auto", "read_parquet", "parquet_scan",
    "read_json", "read_json_auto", "read_text", "read_blob", "glob(",
    "attach", "install ", "load ", "copy ",
    "export database", "import database", "getenv(", "duckdb_settings",
)

SENSITIVE_TOKENS_BY_DIALECT: dict[str, tuple[str, ...]] = {
    "postgres": _TOKENS_COMMON + _TOKENS_POSTGRES,
    "doris": _TOKENS_COMMON + _TOKENS_DORIS,
    "duckdb": _TOKENS_COMMON + _TOKENS_DUCKDB,
}
