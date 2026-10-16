"""集中配置。W0 完成，之后每周只往这里加，不散落在各处。

【踩过的坑】env_file 必须用绝对路径。
写 ".env" 是相对当前工作目录 —— `cd ai && uv run chemq` 会去找 ai/.env，
而 .env 在仓库根目录，于是配置全是默认值、api_key 是空串，
SDK 报一个和"没配 key"毫无关系的 TypeError。
配置文件的查找路径要锚在代码位置上，不要锚在"你从哪个目录敲的命令"上。
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    # 目标引擎。换引擎只改这一个值 —— 业务代码里不该出现 if dialect == ...
    # postgres：W1-W4 的学习环境（Docker 一键重建，评测跑得快，RAG 用 pgvector）
    # doris   ：W5 接入，曙光那边的真实引擎（MySQL 协议，OLAP MPP）
    sql_dialect: Literal["postgres", "doris"] = "postgres"

    pg_dsn: str = "postgresql://chemq_app:chemq@localhost:5432/chemq"
    pg_dsn_readonly: str = "postgresql://chemq_ro_login:chemq@localhost:5432/chemq"

    # Doris 走 MySQL 协议，FE 查询端口默认 9030（不是 8030，那是 HTTP/Stream Load 口）。
    # 只读账号的行级权限不是会话变量，而是绑在 role 上的静态谓词，
    # 所以 W5 这里很可能要变成「一个大区一个 DSN」，见 docs/weekly/w5.md
    doris_dsn: str = ""
    doris_dsn_readonly: str = ""

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
    # aws login --profile chemq 建的那个 profile。凭证是临时的（每 15 分钟自动轮换），
    # 不存长期 access key —— 所以这里只放 profile 名，没有 key。
    aws_profile: str = "chemq"

    # --- RAG（W3）--- 设计说明见 docs/03-rag.md
    embed_model: str = "amazon.titan-embed-text-v2:0"
    embed_dim: int = 1024                  # 必须和 kb_chunk.embedding 的 vector(1024) 一致
    rag_rrf_k: int = 60                    # RRF 的 k：名次的钝化程度
    rag_candidates: int = 20               # 每一路先各取多少条，再融合
    # 交给 Claude 的检索条数。W3 对比（34 块 / 13 题，各跑 3 次）：
    #   5 条：Recall 0.87，引用正确率 0.92，K04 误拒（两条依据只拿到一条）
    #   8 条：Recall 1.00，引用正确率 1.00，误拒 0；擦边题仍 4/4 拒答；成本 +21%
    rag_top_k: int = 8
    # 向量这一路的最低余弦相似度。低于它、且没有标识符/术语字面命中 → 判"无命中"。
    # W3 第 5 步标定（Titan V2，34 块）：有答案题最低 0.408（K13 靠术语信号，不算），
    # 无答案题最高 0.192，取两者中间。换 embedding 模型或知识库大变后要重新跑 make eval-rag 标定。
    rag_min_cosine: float = 0.30
    s3_kb_bucket: str = ""
    ddb_session_table: str = "chemq-sessions"

    @property
    def env_file_path(self) -> Path:
        """给诊断用：告诉你它到底在读哪个文件。"""
        return ROOT / ".env"

    @property
    def dsn(self) -> str:
        """当前方言的可写连接串。"""
        return self.doris_dsn if self.sql_dialect == "doris" else self.pg_dsn

    @property
    def dsn_readonly(self) -> str:
        """当前方言的只读连接串 —— 模型取数用的就是这个。"""
        return self.doris_dsn_readonly if self.sql_dialect == "doris" else self.pg_dsn_readonly


@lru_cache
def settings() -> Settings:
    return Settings()
