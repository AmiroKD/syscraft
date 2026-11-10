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
import time
from collections import OrderedDict

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from syscraft import config
from syscraft.agent.generate import GenerationFlow
from syscraft.agent.graph import BusinessAgent
from syscraft.conversation.chat import ChatService
from syscraft.conversation.store import SessionNotFound, SessionStore
from syscraft.conversation.stream_hub import StreamHub, generation_lock
from syscraft.requirement.intent import RuleBasedIntentResolver
from syscraft.requirement.llm_resolver import LLMIntentResolver
from syscraft.requirement.model_catalog import catalog
from syscraft.runtime import switcher as version_switcher
from syscraft.tools.spring_client import SpringClient, SpringError


def _build_resolver():
    """配置了 LLM_API_KEY 就走模型解析（失败自动降级规则版），否则纯规则版。"""
    if config.LLM_API_KEY:
        return LLMIntentResolver(catalog(online_only=True), fallback=RuleBasedIntentResolver())
    return RuleBasedIntentResolver()


app = FastAPI(title="SysCraft 平台", version="0.1.0")

_client = SpringClient()
_chat = ChatService(_client, _build_resolver())
_agent = BusinessAgent(_client) if config.LLM_API_KEY else None
_sessions = SessionStore()
_hub = StreamHub()
_generation = GenerationFlow(_client)


class _IdentityCache:
    """登录时缓存 token→用户。生成期间产物按设计停机，me() 不可用时的身份兑底。

    只兑底「产物不可达」（503）这一种情况；401 等鉴权错误照常抛。
    平台重启即空——运行也随进程死了，不存在跨重启的重连。"""

    MAX_KEYS = 5_000  # 防涨：token 缓存无过期语义，超限整体重置（演示语义内足够大）

    def __init__(self):
        self._map: dict[str, dict] = {}

    def remember(self, token: str, user: dict) -> None:
        if len(self._map) >= self.MAX_KEYS:
            self._map.clear()
        self._map[token] = user

    def get(self, token: str) -> dict | None:
        return self._map.get(token)


_identities = _IdentityCache()


async def _identity(token: str) -> dict | None:
    """当前用户；产物停机时回退登录缓存，两者都没有则 None。"""
    try:
        user = await _client.me(token)
        _identities.remember(token, user)
        return user
    except SpringError as error:
        # 产物停机有两种表现：连接拒绝（503）或沙箱/网关代理的 502——都算「不在线」
        if error.status in (502, 503):
            return _identities.get(token)
        raise


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    # 会话 id 先透传占位：上下文裁剪与指代消解属于会话层，等那一层落地再用它
    session_id: str | None = Field(default=None, max_length=64)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=64)


class SwitchRequest(BaseModel):
    version: str = Field(min_length=1, max_length=24)


def envelope(data=None, msg: str = "ok", code: int = 0) -> dict:
    return {"code": code, "msg": msg, "data": data}


@app.get("/healthz")
async def healthz() -> dict:
    return envelope({"status": "ok"})


@app.get("/")
async def index():
    """最小前端（web/index.html）。设计是 SPA 单页，这里直接托管。"""
    return FileResponse(config.PROJECT_ROOT / "web" / "index.html")


class _LoginThrottle:
    """登录失败限速（P1-4）：同名+来源 5 次失败锁 5 分钟。内存态、单机语义内自洽——
    多实例部署应换集中存储（记入设计边界）。成功登录即清零。

    超限淘汰必须是「逐条 LRU」而不是整体清空：整体清空会把已锁定账号的计数
    一起抹掉，攻击者用一万个随机用户名各失败一次就能解锁所有人。"""

    MAX_FAILURES = 5
    LOCK_SECONDS = 300
    MAX_KEYS = 10_000

    def __init__(self):
        self._failures: "OrderedDict[str, list[float]]" = OrderedDict()

    def _key(self, username: str, ip: str) -> str:
        return f"{username}@{ip}"

    def locked(self, username: str, ip: str) -> int | None:
        """返回剩余锁定秒数（None = 未锁定）。只读为主：过期条目顺手清掉。"""
        key = self._key(username, ip)
        marks = self._failures.get(key)
        if not marks:
            return None
        now = time.time()
        fresh = [t for t in marks if now - t < self.LOCK_SECONDS]
        if not fresh:
            self._failures.pop(key, None)
            return None
        self._failures[key] = fresh
        self._failures.move_to_end(key)
        if len(fresh) >= self.MAX_FAILURES:
            return int(self.LOCK_SECONDS - (now - fresh[0])) + 1
        return None

    def record_failure(self, username: str, ip: str) -> None:
        key = self._key(username, ip)
        now = time.time()
        marks = [t for t in self._failures.get(key, []) if now - t < self.LOCK_SECONDS]
        marks.append(now)
        self._failures[key] = marks
        self._failures.move_to_end(key)
        while len(self._failures) > self.MAX_KEYS:
            self._failures.popitem(last=False)

    def clear(self, username: str, ip: str) -> None:
        self._failures.pop(self._key(username, ip), None)


_throttle = _LoginThrottle()


@app.post("/api/auth/login")
async def login(payload: LoginRequest, request: Request):
    ip = request.client.host if request.client else "?"
    remaining = _throttle.locked(payload.username, ip)
    if remaining is not None:
        return JSONResponse(status_code=429, content=envelope(
            None, f"失败次数过多，请 {max(remaining // 60, 1)} 分钟后再试", 429))
    try:
        data = await _client.login(payload.username, payload.password)
    except SpringError as error:
        _throttle.record_failure(payload.username, ip)
        return _error(error)
    _throttle.clear(payload.username, ip)
    _identities.remember(data["token"], data["user"])
    return envelope(data)


@app.post("/api/generate/cancel")
async def generate_cancel(authorization: str | None = Header(default=None)):
    """取消进行中的生成（《运行时管理设计》步骤边界语义）。

    取消权：发起人或超管。受理即返回——实际取消落成事件由流水线线程在
    下一个步骤边界完成，前端从 phase/text 事件看到终态。"""
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _identity(token)
    except SpringError as error:
        return _error(error)
    if me is None:
        return JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
    if not generation_lock.locked():
        return JSONResponse(status_code=409, content=envelope(None, "当前没有进行中的生成", 409))
    reason = _generation.request_cancel(me["id"], me.get("roleCode") == "SUPER_ADMIN")
    if reason:
        return JSONResponse(status_code=403, content=envelope(None, reason, 403))
    return envelope({"cancelling": True})


@app.post("/api/chat")
async def chat(payload: ChatRequest, authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    if generation_lock.locked():
        return JSONResponse(status_code=503, content=envelope(None, "系统正在生成中，请稍后再试", 503))

    # ---- Agent 引擎（有 LLM）：生成流优先，业务图接盘 ----
    if _agent is not None:
        try:
            me = await _identity(token)
            if me is None:
                return JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
            session = (_sessions.open(payload.session_id, me["id"])
                       if payload.session_id else _sessions.create(me["id"], payload.message[:20]))
        except SpringError as error:
            return _error(error)
        except SessionNotFound:
            return JSONResponse(status_code=404, content=envelope(None, "会话不存在", 404))

        # 同一会话互斥：非流式通道没有 SSE 的运行守卫，两个并发请求会同时
        # 写同一个 SQLite 会话、互相覆盖槽位（流式通道靠 hub.begin 挡住）
        if not _hub.begin(session.id):
            return JSONResponse(status_code=409, content=envelope(
                None, "该会话有任务进行中，请等待完成", 409))
        try:
            gen_result = await _generation.handle(payload.message, token, session,
                                                  emit=lambda event: _hub.emit(session.id, event),
                                                  me=me)
            if gen_result is not None:
                return envelope({"reply": gen_result.get("reply"), "table": None,
                                 "session_id": session.id})
            final = await _agent.run(payload.message, token, session)
            return envelope({"reply": final.get("reply"), "table": final.get("table"),
                             "session_id": session.id})
        finally:
            _hub.finish(session.id)

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
async def chat_stream(payload: ChatRequest, authorization: str | None = Header(default=None),
                      after_seq: int = Query(default=0, ge=0)):
    """SSE 流式对话（《技术栈》3.2 + 《会话与上下文策略》6.2）。

    - after_seq=0：新消息。会话有任务在身时拒绝 409（前端输入框禁用的后端约束）；
    - after_seq>0：断线重连。不重发请求——只补发缺失事件，继续跟到本轮结束；
      运行已结束则回放全部事件后立即 done（设计 6.2「直接返回最终状态」）。
    """
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    if _agent is None:
        return JSONResponse(status_code=503, content=envelope(None, "未配置模型，流式通道不可用", 503))

    # 身份与会话在进入流之前解析完：错误以普通 JSON 返回，前端不用解析 SSE 里的错误。
    # 注意身份用 _identity：生成期间产物停机，重连不能被 me() 的 502 拦在门外（实测踩过）
    try:
        me = await _identity(token)
        if me is None:
            return JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
        session = (_sessions.open(payload.session_id, me["id"])
                   if payload.session_id else _sessions.create(me["id"], payload.message[:20]))
    except SpringError as error:
        return _error(error)
    except SessionNotFound:
        return JSONResponse(status_code=404, content=envelope(None, "会话不存在", 404))

    sid = session.id
    reconnecting = after_seq > 0

    # 会话忙碌的判定 = hub 运行中 或 生成流水线尾部仍在跑（生成流 dispatch 提前返回，
    # hub 状态已被收殓，只剩 gen_state 槽位能说明还有事件会来）
    if _hub.is_active(sid) or session.get_slot("gen_state") == "running":
        if not reconnecting:
            return JSONResponse(status_code=409, content=envelope(None, "该会话有任务进行中，请等待完成", 409))
    elif reconnecting:
        # 运行已结束的重连：回放既有事件后立刻 done（follow 循环自然走完）
        pass
    elif generation_lock.locked():
        return JSONResponse(status_code=503, content=envelope(None, "另一个会话正在生成系统，请稍后再试", 503))
    elif not _hub.begin(sid):
        return JSONResponse(status_code=409, content=envelope(None, "该会话有任务进行中", 409))

    def emit(event: dict) -> None:
        _hub.emit(sid, event)

    async def dispatch():
        """一条消息的完整派发。生成流可能提前返回而流水线仍在跑——
        follow 循环靠 gen_state 槽位判断还有没有事件会来。"""
        try:
            gen_result = await _generation.handle(payload.message, token, session, emit, me=me)
            if gen_result is not None:
                if gen_result.get("reply"):
                    emit({"type": "text", "text": gen_result["reply"]})
                return
            await _agent.run(payload.message, token, session, on_event=emit)
        except Exception as error:  # noqa: BLE001 任何异常都要变成流里的事件而不是悬空连接
            emit({"type": "error", "message": f"处理失败：{error}"})
        finally:
            # 收殓归 dispatch 生命周期而不是连接生命周期（审查 r5①）：
            # 客户端连接断光也不会留下「running」僵尸卡死会话；
            # 重连连接只读不收殓，拆不掉别人的运行守卫、不会注入提前 done
            _hub.finish(sid)

    task = None if reconnecting else asyncio.create_task(dispatch())

    async def sse():
        async for event in _follow_events(sid, session, _hub, task, after_seq):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _follow_events(sid: str, session, hub: StreamHub, task, after_seq: int):
    """事件跟随循环：新消息连接与断线重连共用。

    - 完成判定：本连接持有 dispatch 任务就看 task.done()；重连连接（task=None）
      看 hub 状态是否已收殓——收殓在 dispatch 的 finally，与连接生死无关。
    - 生成流的 dispatch 提前返回后，靠 gen_state 槽位继续跟到流水线尾部收尾。
    - 退出时向本客户端发 done（各连接自持一份，done 不落 hub 日志）。"""
    last = after_seq
    while True:
        events = hub.replay(sid, last)
        for event in events:
            last = event["seq"]
            yield event
        gen_running = session.get_slot("gen_state") == "running"
        if task is not None:
            dispatch_done = task.done()
        else:
            dispatch_done = hub.status(sid) != "running"
        if dispatch_done and not gen_running:
            if events:
                continue  # 再排一轮，确认没有残余
            break
        if not events:
            await asyncio.sleep(0.15)

    if task is not None and task.done() and task.exception() is not None:
        yield {"type": "error", "message": str(task.exception())}
    yield {"type": "done", "sessionId": sid}


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


# ---- 版本切换与回滚（运行时 5 节；仅超管；Spring 管状态，平台管进程）----

@app.get("/api/runtime/versions")
async def runtime_versions(authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _identity(token)
    except SpringError as error:
        return _error(error)
    if me is None or me.get("roleCode") != "SUPER_ADMIN":
        return JSONResponse(status_code=403, content=envelope(None, "仅超管可查看版本清单", 403))
    return envelope(version_switcher.list_versions())


@app.post("/api/runtime/switch")
async def runtime_switch(payload: SwitchRequest, authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    if generation_lock.locked():
        return JSONResponse(status_code=503, content=envelope(None, "系统正在生成中，不能切换版本", 503))
    try:
        me = await _identity(token)
    except SpringError as error:
        return _error(error)
    if me is None or me.get("roleCode") != "SUPER_ADMIN":
        return JSONResponse(status_code=403, content=envelope(None, "仅超管可切换版本", 403))

    import asyncio as _asyncio
    try:
        result = await _asyncio.to_thread(version_switcher.switch, payload.version)
    except version_switcher.SwitchError as error:
        return JSONResponse(status_code=409, content=envelope(None, str(error), 409))
    return envelope(result)


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
