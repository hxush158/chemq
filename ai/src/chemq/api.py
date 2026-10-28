"""FastAPI 服务（阶段二 W4 部署到 Azure Container Apps，见 docs/10-flagship-plan.md）。

和上游网关的分工（原来的 .NET 网关已经移除，C# 另外单独学）：
    上游网关    认证、数据权限(user -> scope)、审计、限流、幂等
    本服务      AI 编排与取数，只信任网关传来的 X-Chemq-Scope / X-Chemq-User

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
    as_of: str | None = None          # 不传就用领域包的 eval.as_of


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/ask", response_model=Answer)
def ask(
    req: AskRequest,
    x_chemq_scope: str | None = Header(default=None),
    x_chemq_user: str | None = Header(default=None),
) -> Answer:
    if not x_chemq_user:
        raise HTTPException(401, "缺少 X-Chemq-User；请求必须经过网关")
    return answer(req.question, as_of=req.as_of, scope=x_chemq_scope)
