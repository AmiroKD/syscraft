"""平台 HTTP 入口 —— §5 第 5 步的最窄链路。

链路：前端 → 本服务（登录代理 / 对话）→ 产物侧 Spring → 回到表格。
平台不连数据库；令牌只透传，不在这里解析或续签。
"""

from __future__ import annotations

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from syscraft import config
from syscraft.conversation.chat import ChatService
from syscraft.requirement.intent import RuleBasedIntentResolver
from syscraft.requirement.llm_resolver import LLMIntentResolver
from syscraft.requirement.model_catalog import catalog
from syscraft.tools.spring_client import SpringClient, SpringError


def _build_resolver():
    """配置了 LLM_API_KEY 就走模型解析（失败自动降级规则版），否则纯规则版。"""
    if config.LLM_API_KEY:
        return LLMIntentResolver(catalog(), fallback=RuleBasedIntentResolver())
    return RuleBasedIntentResolver()


app = FastAPI(title="SysCraft 平台", version="0.1.0")

_client = SpringClient()
_chat = ChatService(_client, _build_resolver())


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    # 会话 id 先透传占位：上下文裁剪与指代消解属于会话层，等那一层落地再用它
    session_id: str | None = Field(default=None, max_length=64)


def envelope(data=None, msg: str = "ok", code: int = 0) -> dict:
    return {"code": code, "msg": msg, "data": data}


@app.get("/healthz")
async def healthz() -> dict:
    return envelope({"status": "ok"})


@app.post("/api/auth/login")
async def login(payload: LoginRequest) -> dict:
    try:
        data = await _client.login(payload.username, payload.password)
    except SpringError as error:
        return _error(error)
    return envelope(data)


@app.post("/api/chat")
async def chat(payload: ChatRequest, authorization: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))

    reply = await _chat.handle(payload.message, token, payload.session_id)
    table = None
    if reply.table is not None:
        table = {
            "columns": [{"key": c.key, "title": c.title, "type": c.type} for c in reply.table.columns],
            "rows": reply.table.rows,
            "total": reply.table.total,
            "page": reply.table.page,
            "size": reply.table.size,
            "truncated": reply.table.truncated,
        }
    return envelope({"reply": reply.reply, "table": table, "session_id": payload.session_id})


def _bearer(header: str | None) -> str | None:
    if not header:
        return None
    value = header.strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value or None


def _error(error: SpringError) -> JSONResponse:
    # 产物侧的 401 原样透传：前端据此清令牌回登录页，而不是当成普通错误提示
    status = error.status if error.status in (401, 403) else 502
    return JSONResponse(status_code=status, content=envelope(None, error.message, error.code or status))
