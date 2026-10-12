"""向量化 —— Bedrock Titan Text Embeddings V2。

凭证：用 `aws login --profile chemq` 拿到的临时凭证（每 15 分钟自动轮换，最长 12 小时），
代码里、.env 里都不放任何 AWS key。boto3 要 >=1.41 且装了 CRT 扩展（boto3[crt]）
才能直接读这种 profile —— 见 pyproject 的 aws extra。

万一 boto3 读不了（报 login / CRT 相关的错），兜底办法：在 ~/.aws/config 的
[profile chemq] 下加一行
    credential_process = aws configure export-credentials --profile chemq --format process
这样任何版本的 boto3 都会去问 aws CLI 要凭证。

Embedder 是个 Protocol：测试里换成假的，不连 AWS 也能测 ingest。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .config import settings

LOGIN_HINT = (
    "拿不到 AWS 凭证。先在终端跑：aws login --profile chemq "
    "（临时凭证最长 12 小时，过期了重新跑一次即可）。"
)


@dataclass
class Embedding:
    vector: list[float]
    tokens: int                  # Titan 返回的 inputTextTokenCount，写进 kb_chunk.token_count


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, text: str) -> Embedding: ...


class TitanEmbedder:
    def __init__(self, client: Any = None, *, model: str | None = None, dim: int | None = None):
        s = settings()
        self.model = model or s.embed_model
        self.dim = dim or s.embed_dim
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import boto3  # noqa: PLC0415
            except ImportError as e:
                raise RuntimeError("没装 boto3。先跑：make install-rag") from e
            s = settings()
            session = boto3.Session(profile_name=s.aws_profile, region_name=s.aws_region)
            self._client = session.client("bedrock-runtime")
        return self._client

    def embed(self, text: str) -> Embedding:
        body = json.dumps({"inputText": text, "dimensions": self.dim, "normalize": True})
        try:
            resp = self.client.invoke_model(
                modelId=self.model, body=body,
                contentType="application/json", accept="application/json",
            )
        except Exception as e:  # noqa: BLE001 —— botocore 的异常类型很多，统一加提示再抛
            raise RuntimeError(f"{_hint(e)}\n原始错误：{type(e).__name__}: {e}") from e
        out = json.loads(resp["body"].read())
        vec = out["embedding"]
        if len(vec) != self.dim:
            # 和 kb_chunk.embedding vector(1024) 对不上，写库时才报错就晚了
            raise ValueError(f"向量维度 {len(vec)} ≠ 配置的 {self.dim}")
        return Embedding(vector=vec, tokens=int(out.get("inputTextTokenCount", 0)))


def _hint(e: Exception) -> str:
    name, msg = type(e).__name__, str(e)
    if any(k in name + msg for k in ("NoCredentials", "Token", "Expired", "login", "SSO")):
        return LOGIN_HINT
    if "ProfileNotFound" in name:
        return "找不到 profile chemq。先跑：aws configure set region ap-southeast-2 --profile chemq"
    if "AccessDenied" in name + msg:
        return "AWS 拒绝访问：确认 Bedrock 里 Titan Text Embeddings V2 可用、区域是 ap-southeast-2。"
    if "CRT" in msg or "crt" in msg:
        return "boto3 缺 CRT 扩展。先跑：make install-rag"
    return "调用 Titan 失败。"
