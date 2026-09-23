"""集中配置。W0 完成，之后每周只往这里加，不散落在各处。

【踩过的坑】env_file 必须用绝对路径。
写 ".env" 是相对当前工作目录 —— `cd ai && uv run chemq` 会去找 ai/.env，
而 .env 在仓库根目录，于是配置全是默认值、api_key 是空串，
SDK 报一个和"没配 key"毫无关系的 TypeError。
配置文件的查找路径要锚在代码位置上，不要锚在"你从哪个目录敲的命令"上。
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py -> chemq -> src -> ai -> 仓库根
ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # 元组：后面的覆盖前面的。根目录的 .env 是主配置，
        # ai/.env 存在时可以就近覆盖（平时不需要建）。
        env_file=(ROOT / ".env", ROOT / "ai" / ".env"),
        env_file_encoding="utf-8",
        env_prefix="CHEMQ_",
        extra="ignore",
    )

    # --- Claude ---
    anthropic_api_key: str = ""
    # 留空则走官方 https://api.anthropic.com。
    # 只有在你确实要走第三方网关/自建代理时才设 CHEMQ_ANTHROPIC_BASE_URL。
    # 注意：SDK 还会读裸的 ANTHROPIC_BASE_URL 环境变量 —— 那个是最常见的"我明明没配却连不上"的元凶。
    anthropic_base_url: str = ""
    model_router: str = "claude-haiku-4-5-20251001"   # 便宜快：意图路由/改写
    model_sql: str = "claude-sonnet-5"                # 主力：Text-to-SQL / 叙述
    model_critic: str = "claude-sonnet-5"             # 复核
    max_output_tokens: int = 2048
    # output_config.effort: low / medium / high(默认) / xhigh / max
    # 控制这次响应总共花多少 output token（含思考）。留空 = 不传，用 API 默认值。
    # 注意：Haiku 4.5 不支持 effort；只有 Sonnet 4.6+ / Opus 4.5+ / Fable / Mythos 支持。
    effort: str = ""

    # --- 数据库 ---
    pg_dsn: str = "postgresql://chemq_app:chemq@localhost:5432/chemq"
    pg_dsn_readonly: str = "postgresql://chemq_ro_login:chemq@localhost:5432/chemq"
    statement_timeout_ms: int = 8000
    max_rows: int = 500

    # --- 护栏 ---
    allowed_relations: tuple[str, ...] = (
        "chem.v_plant_daily", "chem.v_energy_daily",
        "chem.v_sales_daily", "chem.v_monthly_summary",
        "chem.dim_date", "chem.dim_plant", "chem.dim_product",
        "chem.dim_customer", "chem.dim_energy_type",
    )

    # --- AWS ---
    aws_region: str = "ap-southeast-2"        # Sydney：离 Auckland 最近
    s3_kb_bucket: str = ""
    ddb_session_table: str = "chemq-sessions"

    @property
    def env_file_path(self) -> Path:
        """给诊断用：告诉你它到底在读哪个文件。"""
        return ROOT / ".env"


@lru_cache
def settings() -> Settings:
    return Settings()
