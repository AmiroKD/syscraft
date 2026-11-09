"""平台 HTTP 入口。

两条对话通道：
- POST /api/chat        一次性 JSON（兼容既有前端）
- POST /api/chat/stream SSE 流式（设计《技术栈》3.2：fetch + ReadableStream 自解析）

引擎选择：配了 LLM_API_KEY 走工具循环 Agent（LangGraph 状态机），
没配则回退规则版直连管道（仅开发自测，不落会话）。
会话持久化按《会话与上下文策略》1.1：每会话一个 SQLite 文件。
"""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from syscraft import config
from syscraft.agent.graph import BusinessAgent
from syscraft.conversation.chat import ChatService
from syscraft.conversation.store import SessionNotFound, SessionStore
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
_agent = BusinessAgent(_client, catalog()) if config.LLM_API_KEY else None
_sessions = SessionStore()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    # 会话 id 先透传占位：上下文裁剪与指代消解属于会话层，等那一层落地再用它
    session_id: str | None = Field(default=None, max_length=64)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=64)


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
async def chat(payload: ChatRequest, authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))

    # ---- Agent 引擎（有 LLM）：跑图，一次返回最终结果 ----
    if _agent is not None:
        try:
            me = await _client.me(token)
            session = (_sessions.open(payload.session_id, me["id"])
                       if payload.session_id else _sessions.create(me["id"], payload.message[:20]))
        except SpringError as error:
            return _error(error)
        except SessionNotFound:
            return JSONResponse(status_code=404, content=envelope(None, "会话不存在", 404))

        final = await _agent.run(payload.message, token, session)
        return envelope({"reply": final.get("reply"), "table": final.get("table"),
                         "session_id": session.id})

    # ---- 规则版回退（无 LLM，仅开发自测）：消息不落会话，见 chat.py ----
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


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatRequest, authorization: str | None = Header(default=None)):
    """SSE 流式对话。事件形态见《智能体工具协议》3 节，type 与前端卡片一一对应。"""
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    if _agent is None:
        return JSONResponse(status_code=503, content=envelope(None, "未配置模型，流式通道不可用", 503))

    # 身份与会话在进入流之前解析完：错误以普通 JSON 返回，前端不用解析 SSE 里的错误
    try:
        me = await _client.me(token)
        session = (_sessions.open(payload.session_id, me["id"])
                   if payload.session_id else _sessions.create(me["id"], payload.message[:20]))
    except SpringError as error:
        return _error(error)
    except SessionNotFound:
        return JSONResponse(status_code=404, content=envelope(None, "会话不存在", 404))

    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    session_id = session.id

    async def generate():
        seq = 0

        def emit(event: dict) -> None:
            queue.put_nowait(event)

        task = asyncio.create_task(_agent.run(payload.message, token, session, on_event=emit))
        # 循环纪律：任务没完就带超时等事件；任务完了先排干队列再退出——
        # 否则最后几个事件会被 break 吞掉（asyncio 的经典竞态）
        while True:
            if not task.done():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue
            else:
                try:
                    event = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            seq += 1
            yield f"data: {json.dumps({'seq': seq, **event}, ensure_ascii=False)}\n\n"
        if task.exception() is not None:
            seq += 1
            yield f"data: {json.dumps({'seq': seq, 'type': 'error', 'message': str(task.exception())}, ensure_ascii=False)}\n\n"
            final = None
        else:
            final = task.result()
        seq += 1
        yield f"data: {json.dumps({'seq': seq, 'type': 'done', 'sessionId': session_id, 'reply': final.get('reply') if final else None}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- 会话管理（《会话与上下文策略》4 节：列表 / 历史 / 重命名 / 删除）----

@app.get("/api/sessions")
async def list_sessions(authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _client.me(token)
    except SpringError as error:
        return _error(error)
    return envelope(_sessions.list_for(me["id"]))


@app.get("/api/sessions/{session_id}/messages")
async def session_messages(session_id: str, authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _client.me(token)
        session = _sessions.open(session_id, me["id"])
    except SpringError as error:
        return _error(error)
    except SessionNotFound:
        return JSONResponse(status_code=404, content=envelope(None, "会话不存在", 404))
    return envelope({"sessionId": session_id, "title": session.meta().get("title", ""),
                     "messages": session.messages()})


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, payload: RenameRequest,
                         authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _client.me(token)
        session = _sessions.open(session_id, me["id"])
    except SpringError as error:
        return _error(error)
    except SessionNotFound:
        return JSONResponse(status_code=404, content=envelope(None, "会话不存在", 404))
    with session._conn() as conn:
        conn.execute("UPDATE meta SET value=? WHERE key='title'", (payload.title[:20],))
        conn.commit()
    return envelope({"sessionId": session_id, "title": payload.title[:20]})


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _client.me(token)
        _sessions.delete(session_id, me["id"])
    except SpringError as error:
        return _error(error)
    except SessionNotFound:
        return JSONResponse(status_code=404, content=envelope(None, "会话不存在", 404))
    return envelope({"deleted": session_id})


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
