"""方言表：同一条规则在不同 SQL 引擎上的写法。属于引擎层，和领域包无关。

提示词模板里用 {{ENGINE}}、{{ENGINE_NAME}}、{{MONTH_FMT}} 引用这里的值（见 chemq/prompts）。
W1 第 4 步在这里加 duckdb / snowflake，敏感 token 表也会从 sqlguard 挪过来。
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
}


def profile(dialect: str) -> dict[str, str]:
    try:
        return DIALECT_PROFILE[dialect]
    except KeyError:
        raise ValueError(f"没有 {dialect!r} 的方言配置，可选：{sorted(DIALECT_PROFILE)}") from None
