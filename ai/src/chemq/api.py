"""W5 交付物：FastAPI 服务，部署到 Lambda。

和 .NET 网关的分工（这是 W5 要想清楚的架构问题）：
    .NET 网关   认证(Cognito JWT 校验)、数据权限(user -> region)、审计、限流、幂等
    本服务      AI 编排与取数，只信任网关传来的 X-Chemq-Region / X-Chemq-User

为什么不让 Python 服务直接做认证：企业里认证/授权/审计通常已经有统一的一层，
AI 服务应该是可替换的组件，而不是另起一套权限体系。这个回答在架构面试里很加分。
"""
from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from .pipeline import answer
from .schemas import Answer

app = FastAPI(title="ChemQ AI Service", version="0.1.0")


class AskRequest(BaseModel):
    question: str
    as_of: str = "2026-09-14"


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/ask", response_model=Answer)
def ask(
    req: AskRequest,
    x_chemq_region: str | None = Header(default=None),
    x_chemq_user: str | None = Header(default=None),
) -> Answer:
    if not x_chemq_user:
        raise HTTPException(401, "缺少 X-Chemq-User；请求必须经过网关")
    return answer(req.question, as_of=req.as_of, region=x_chemq_region)
