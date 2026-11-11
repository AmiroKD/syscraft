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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from syscraft import config, llm_settings
from syscraft.agent.generate import GenerationFlow
from syscraft.agent.graph import MAX_HISTORY_ROWS, BusinessAgent
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
# 前端（Vue 3）构建产物：web/dist。产物随仓库提交，所以只有 Python 环境也能起服务；
# 改了前端源码要重新 `cd web && npm run build`。
# check_dir=False：先起服务、后构建也能生效，不用为了一个目录存在与否再重启一次。
_DIST = config.PROJECT_ROOT / "web" / "dist"
app.mount("/assets", StaticFiles(directory=str(_DIST / "assets"), check_dir=False),
          name="assets")

# 恢复上次选择的模型（四个 LLM 入口都在调用时读 config.LLM_MODEL，这里改全局即可）
llm_settings.load()

_client = SpringClient()
_chat = ChatService(_client, _build_resolver())
# 始终构造：设置面板允许运行时补填 API Key，启动时没配也不该把 Agent 永久钉死为 None
_agent = BusinessAgent(_client)
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
    # 续传（resume=1）不带消息：那一次请求只接管事件流，不派发新任务。
    # 是否为空在端点里判——空消息 + 非续传仍然是非法请求。
    message: str = Field(default="", max_length=500)
    session_id: str | None = Field(default=None, max_length=64)


class SessionRef(BaseModel):
    """只带会话 id 的操作（暂停）。"""

    session_id: str = Field(min_length=1, max_length=64)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=64)


class SwitchRequest(BaseModel):
    version: str = Field(min_length=1, max_length=24)


class LlmSettingsRequest(BaseModel):
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=300)
    api_key: str | None = Field(default=None, alias="apiKey", max_length=500)
    model: str = Field(min_length=1, max_length=100)
    model_config = {"populate_by_name": True}


# 模型接入是平台级动作（全局生效），与生成/改结构同一条权限线
LLM_CONFIG_ROLES = {"SUPER_ADMIN", "ADMIN"}

# 暂停后补进历史的说明。措辞要同时说清两件事：这轮是被人停的（不是失败），
# 以及接下来能做什么（重新发送）——只说「已停止」会让人以为要重开一个会话
PAUSE_NOTE = "已暂停本次回答。可以继续提问，或补充条件后重新发送。"


def envelope(data=None, msg: str = "ok", code: int = 0) -> dict:
    return {"code": code, "msg": msg, "data": data}


@app.get("/healthz")
async def healthz() -> dict:
    return envelope({"status": "ok"})


@app.get("/")
async def index():
    """前端（web/dist）：Vue 3 单页，由平台自己托管——浏览器只认 8090 一个源。"""
    page = _DIST / "index.html"
    if not page.exists():
        return HTMLResponse(
            "<meta charset='utf-8'><h1>前端尚未构建</h1>"
            "<p>在 <code>agent-platform/web</code> 下执行 "
            "<code>npm install &amp;&amp; npm run build</code> 后刷新本页。</p>",
            status_code=503,
        )
    return FileResponse(page, headers={"Cache-Control": "no-store"})


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


@app.get("/api/auth/me")
async def current_user(authorization: str | None = Header(default=None)):
    """当前登录用户（前端进主界面时取身份）。

    平台是浏览器唯一入口（《接口契约》1 节），所以这个端点必须由平台转发，
    而不是让前端直连产物：前端只认 8090 一个源，CORS 与令牌范围都不越界。
    """
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        user = await _identity(token)
    except SpringError as error:
        return _error(error)
    if user is None:
        return JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
    return envelope(user)


@app.get("/api/settings/llm")
async def get_llm_setting(authorization: str | None = Header(default=None)):
    """公开状态：平台是否已配置 + 常用模型建议。不回传服务端 URL/Key/模型。"""
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _identity(token)
    except SpringError as error:
        return _error(error)
    if me is None:
        return JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
    return envelope(llm_settings.public_snapshot())


@app.put("/api/settings/llm")
async def update_llm_setting(payload: LlmSettingsRequest,
                             authorization: str | None = Header(default=None)):
    """保存 Base URL / API Key / 模型名，立即对全部 LLM 入口生效。"""
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _identity(token)
    except SpringError as error:
        return _error(error)
    if me is None:
        return JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
    if me.get("roleCode") not in LLM_CONFIG_ROLES:
        return JSONResponse(status_code=403, content=envelope(None, "仅管理员可修改模型配置", 403))
    try:
        data = llm_settings.set_config(payload.base_url, payload.api_key, payload.model)
    except llm_settings.LlmConfigError as error:
        return JSONResponse(status_code=400, content=envelope(None, str(error), 400))
    except llm_settings.LlmSettingsError as error:
        return JSONResponse(status_code=500, content=envelope(None, str(error), 500))
    return envelope(data)


@app.post("/api/settings/llm/test")
async def test_llm_setting(payload: LlmSettingsRequest,
                           authorization: str | None = Header(default=None)):
    """用最小补全测试 URL / Key / 模型；失败也返回 200 + ok=false，便于前端展示原因。"""
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _identity(token)
    except SpringError as error:
        return _error(error)
    if me is None:
        return JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
    if me.get("roleCode") not in LLM_CONFIG_ROLES:
        return JSONResponse(status_code=403, content=envelope(None, "仅管理员可测试模型配置", 403))
    try:
        data = await llm_settings.test_connection(payload.base_url, payload.api_key, payload.model)
    except llm_settings.LlmConfigError as error:
        return JSONResponse(status_code=400, content=envelope(None, str(error), 400))
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
    if not payload.message.strip():
        return JSONResponse(status_code=400, content=envelope(None, "消息不能为空", 400))
    if generation_lock.locked():
        return JSONResponse(status_code=503, content=envelope(None, "系统正在生成中，请稍后再试", 503))

    # ---- Agent 引擎（有 LLM）：生成流优先，业务图接盘 ----
    if _agent is not None and config.LLM_API_KEY:
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


@app.post("/api/chat/pause")
async def chat_pause(payload: SessionRef, authorization: str | None = Header(default=None)):
    """暂停本轮回答（《暂停与刷新恢复设计》2 节）。

    语义是「停下并把已经产出的留下」，不是「冻结后原地继续」：LLM 是一次非流式
    补全，服务端没有可续的中间态，恢复等于重发一遍请求。

    受理即返回：真正的收殓在 dispatch 的 CancelledError 分支里（落表格、补一句
    暂停说明），前端从 paused 事件看到终态。生成流水线不走这里——它有步骤边界
    上的「取消生成」，取消任务动不了 to_thread 里的编译线程。"""
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _identity(token)
        if me is None:
            return JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
        session = _sessions.open(payload.session_id, me["id"])
    except SpringError as error:
        return _error(error)
    except SessionNotFound:
        return JSONResponse(status_code=404, content=envelope(None, "会话不存在", 404))
    if session.get_slot("gen_state") == "running":
        return JSONResponse(status_code=409, content=envelope(
            None, "系统正在生成，请用进度面板上的「取消生成」", 409))
    if not _hub.request_pause(session.id):
        return JSONResponse(status_code=409, content=envelope(None, "当前没有进行中的回答", 409))
    return envelope({"pausing": True})


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatRequest, authorization: str | None = Header(default=None),
                      after_seq: int = Query(default=0, ge=0),
                      resume: bool = Query(default=False)):
    """SSE 流式对话（《技术栈》3.2 + 《会话与上下文策略》6.2）。

    - after_seq=0 且不带 resume：新消息。会话有任务在身时拒绝 409（前端输入框
      禁用的后端约束）；
    - after_seq>0：断线重连。不重发请求——只补发缺失事件，继续跟到本轮结束；
      运行已结束则回放全部事件后立即 done（设计 6.2「直接返回最终状态」）；
    - resume=1：刷新后的接管。seq 由客户端从 /api/sessions/{id}/messages 的
      run.seq 取（可以是 0），同样只跟随、不派发——刷新不该等于重发一条消息。
    """
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    if _agent is None or not config.LLM_API_KEY:
        return JSONResponse(status_code=503, content=envelope(
            None, "未配置模型，请在「设置 → 模型服务」填写 API Key", 503))

    following = bool(resume) or after_seq > 0
    if following and not payload.session_id:
        return JSONResponse(status_code=400, content=envelope(None, "续传必须带 session_id", 400))
    if not following and not payload.message.strip():
        return JSONResponse(status_code=400, content=envelope(None, "消息不能为空", 400))

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
    # 会话忙碌的判定 = hub 运行中 或 生成流水线尾部仍在跑（生成流 dispatch 提前返回，
    # hub 状态已被收殓，只剩 gen_state 槽位能说明还有事件会来）
    decision = _stream_decision(
        active=_hub.is_active(sid) or session.get_slot("gen_state") == "running",
        following=following,
        generation_locked=generation_lock.locked(),
    )
    if decision == "conflict":
        return JSONResponse(status_code=409, content=envelope(None, "该会话有任务进行中，请等待完成", 409))
    if decision == "generation_locked":
        return JSONResponse(status_code=503, content=envelope(
            None, "另一个会话正在生成系统，请稍后再试", 503))
    started = decision == "start"
    if started and not _hub.begin(sid):
        return JSONResponse(status_code=409, content=envelope(None, "该会话有任务进行中", 409))

    # 首个事件回传会话 id：新会话的首轮没有 id，前端拿到它才能
    # 「断线续传」而不是重发（重发会再建一个会话——历史会话裂开的另一半原因）
    if started:
        _hub.emit(sid, {"type": "session", "sessionId": sid})

    def emit(event: dict) -> None:
        _hub.emit(sid, event)

    async def dispatch():
        """一条消息的完整派发。生成流可能提前返回而流水线仍在跑——
        follow 循环靠 gen_state 槽位判断还有没有事件会来。"""
        try:
            gen_result = await _generation.handle(payload.message, token, session, emit, me=me)
            if gen_result is not None:
                if gen_result.get("event"):
                    # 计划/追问是结构化卡片（可点确认、可填表单），按类型回传；
                    # 其余回复仍走纯文本
                    emit(gen_result["event"])
                elif gen_result.get("reply"):
                    emit({"type": "text", "text": gen_result["reply"]})
                return
            await _agent.run(payload.message, token, session, on_event=emit)
        except asyncio.CancelledError:
            # 暂停：取消点通常落在 await 中的 LLM / 工具请求里。不重新抛出——
            # 抛出会让 task 变成 cancelled，_follow_events 取 exception() 时二次炸掉，
            # 连接就收不到 paused 和 done 两个终态事件了（见 _settle_pause）
            _settle_pause(sid, session, _hub)
        except Exception as error:  # noqa: BLE001 任何异常都要变成流里的事件而不是悬空连接
            emit({"type": "error", "message": f"处理失败：{error}"})
        finally:
            # 收殓归 dispatch 生命周期而不是连接生命周期（审查 r5①）：
            # 客户端连接断光也不会留下「running」僵尸卡死会话；
            # 重连连接只读不收殓，拆不掉别人的运行守卫、不会注入提前 done
            _hub.finish(sid)

    task = asyncio.create_task(dispatch()) if started else None
    if task is not None:
        _hub.attach_task(sid, task)

    async def sse():
        async for event in _follow_events(sid, session, _hub, task, after_seq):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream_decision(active: bool, following: bool, generation_locked: bool) -> str:
    """这条流式请求该做什么：start（派发新消息）/ follow（只跟随）/ 拒绝。

    纯函数：把「会话是否在跑 × 是不是续传 × 有没有生成占着全局锁」的四种组合
    摊开写，单测能直接覆盖，不用起 HTTP、不用连库（《暂停与刷新恢复设计》4 节）。
    """
    if active:
        return "follow" if following else "conflict"
    if following:
        # 运行已结束的续传：回放既有事件后立刻 done（follow 循环自然走完）
        return "follow"
    if generation_locked:
        return "generation_locked"
    return "start"


def _settle_pause(sid: str, session, hub: StreamHub) -> None:
    """暂停的收殓：把「用户已经看见的东西」落库，再补一句暂停说明。

    表格是在本轮结束时才统一落库的，不补这一步，暂停后刷新页面会发现刚才那张表
    凭空消失；补说明则让人知道这轮不是失败，是被人为停下的（notice 类型不进
    LLM 历史，见 context._history_text）。
    """
    try:
        events = hub.replay(sid)
        if any(e.get("type") == "text" for e in events):
            return  # 回答已经出来了，暂停来晚了，不制造多余的说明
        table = next((e.get("table") for e in reversed(events)
                      if e.get("type") == "table" and e.get("table")), None)
        if table:
            _persist_live_table(session, table)
        note = PAUSE_NOTE
        session.append("assistant", "notice", note)
        hub.emit(sid, {"type": "paused", "text": note})
    except Exception:  # noqa: BLE001 收殓失败不能反过来炸掉这条连接，前端至少能收到 done
        pass


def _persist_live_table(session, table: dict) -> None:
    """把暂停瞬间的表格写进历史（与 graph._persist_table 同一份结构）。

    去重：上一轮如果已经把同一实体的表格落库了，这里不再写第二张——多轮查询
    只该在历史里留下最终那张表。"""
    try:
        last = session.messages(limit=1)
    except Exception:  # noqa: BLE001 读不到历史就不做去重，写总比丢强
        last = []
    if last and last[-1].get("type") == "table" \
            and (last[-1].get("content") or {}).get("entity") == table.get("entity"):
        return
    rows = (table.get("rows") or [])[:MAX_HISTORY_ROWS]
    content = {"entity": table.get("entity"), "display": table.get("display"),
               "columns": table.get("columns") or [], "rows": rows,
               "total": table.get("total", len(rows)), "displayed": len(rows)}
    if table.get("kind"):
        content["kind"] = table["kind"]
    if table.get("chart"):
        content["chart"] = table["chart"]
    session.append("assistant", "table", content, tool_call=table.get("toolCall"))


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
        # 用 _identity 而不是直连 me()：生成期间产物会停机，侧栏不能跟着 502——
        # 会话是平台自己的库，产物不在线也应能翻历史
        me = await _identity(token)
    except SpringError as error:
        return _error(error)
    if me is None:
        return JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
    return envelope(_sessions.list_for(me["id"]))


@app.get("/api/sessions/{session_id}/messages")
async def session_messages(session_id: str, authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _identity(token)
        if me is None:
            return JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
        session = _sessions.open(session_id, me["id"])
    except SpringError as error:
        return _error(error)
    except SessionNotFound:
        return JSONResponse(status_code=404, content=envelope(None, "会话不存在", 404))
    # 运行状态回传给前端：刷新后据此决定「继续接管这条流」还是「只看历史」。
    # seq 是接管点（中枢的跨轮单调序号），mode 决定刷新后是显示打字指示还是进度面板
    generating = session.get_slot("gen_state") == "running"
    run = {"active": _hub.is_active(session_id) or generating,
           "seq": _hub.seq(session_id),
           "mode": "generation" if generating else "chat"}
    return envelope({"sessionId": session_id, "title": session.meta().get("title", ""),
                     "messages": session.messages(), "run": run})


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, payload: RenameRequest,
                         authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _identity(token)
        if me is None:
            return JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
        session = _sessions.open(session_id, me["id"])
    except SpringError as error:
        return _error(error)
    except SessionNotFound:
        return JSONResponse(status_code=404, content=envelope(None, "会话不存在", 404))
    session.set_title(payload.title)
    return envelope({"sessionId": session_id, "title": payload.title[:20]})


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token is None:
        return JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _identity(token)
        if me is None:
            return JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
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


# ---- 管理员面板转发（账号 / 角色 / 表级授权）----
# 权限操作走「带角色校验的专用界面」，不走对话：高危操作的守门人应该是
# 结构化界面 + 产物侧能力校验，而不是一条会听话的模型链路。
# 平台只把入口收窄到管理员（两道门的第一道）；真正的能力门槛（MANAGE_USER、
# 层级不可越权建号）与操作审计都在产物侧 OperatorContext / adm_audit，平台不复制权限模型。
# 端点显式列出——平台绝不转发任意路径，grant bootstrap 等流水线专用端点不在其中。

ADMIN_PANEL_ROLES = {"SUPER_ADMIN", "ADMIN"}


async def _admin_guard(authorization: str | None):
    """管理员端点的公共前置。返回 (token, None) 或 (None, JSONResponse)。"""
    token = _bearer(authorization)
    if token is None:
        return None, JSONResponse(status_code=401, content=envelope(None, "未提供令牌", 401))
    try:
        me = await _identity(token)
    except SpringError as error:
        return None, _error(error)
    if me is None:
        return None, JSONResponse(status_code=503, content=envelope(None, "产物服务不可达且无法确认身份", 503))
    if me.get("roleCode") not in ADMIN_PANEL_ROLES:
        return None, JSONResponse(status_code=403, content=envelope(None, "仅管理员可访问账号管理", 403))
    return token, None


async def _json_body(request: Request) -> dict | None:
    """读 JSON 体；字段级校验在产物侧，平台只保证「是个对象」。"""
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _admin_error(error: SpringError) -> JSONResponse:
    # 管理接口的 400（密码长度、角色不存在等）是调用方可修复的错误，原样透传
    # 而不是笼统的 502——前端要拿 msg 提示用户改输入；401/403 语义同既有约定
    status = error.status if error.status in (400, 401, 403, 404, 409) else 502
    return JSONResponse(status_code=status, content=envelope(None, error.message, error.code or status))


async def _admin_proxy(method: str, path: str, payload: dict | None, token: str,
                       params: dict | None = None):
    try:
        data = await _client.passthrough(method, path, payload, token, params=params)
    except SpringError as error:
        return _admin_error(error)
    return envelope(data)


@app.get("/api/admin/users")
async def admin_list_users(request: Request, authorization: str | None = Header(default=None)):
    token, denied = await _admin_guard(authorization)
    if denied:
        return denied
    return await _admin_proxy("GET", "/api/admin/users", None, token,
                              params=dict(request.query_params))


@app.post("/api/admin/users")
async def admin_create_user(request: Request, authorization: str | None = Header(default=None)):
    token, denied = await _admin_guard(authorization)
    if denied:
        return denied
    payload = await _json_body(request)
    if payload is None:
        return JSONResponse(status_code=400, content=envelope(None, "请求体必须是 JSON 对象", 400))
    return await _admin_proxy("POST", "/api/admin/users", payload, token)


@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(user_id: int, request: Request,
                            authorization: str | None = Header(default=None)):
    token, denied = await _admin_guard(authorization)
    if denied:
        return denied
    payload = await _json_body(request)
    if payload is None:
        return JSONResponse(status_code=400, content=envelope(None, "请求体必须是 JSON 对象", 400))
    return await _admin_proxy("PATCH", f"/api/admin/users/{user_id}", payload, token)


@app.post("/api/admin/users/{user_id}/password")
async def admin_reset_password(user_id: int, authorization: str | None = Header(default=None)):
    token, denied = await _admin_guard(authorization)
    if denied:
        return denied
    return await _admin_proxy("POST", f"/api/admin/users/{user_id}/password", None, token)


@app.get("/api/admin/roles")
async def admin_list_roles(authorization: str | None = Header(default=None)):
    token, denied = await _admin_guard(authorization)
    if denied:
        return denied
    return await _admin_proxy("GET", "/api/admin/roles", None, token)


@app.get("/api/admin/grants")
async def admin_list_grants(request: Request, authorization: str | None = Header(default=None)):
    token, denied = await _admin_guard(authorization)
    if denied:
        return denied
    return await _admin_proxy("GET", "/api/admin/grants", None, token,
                              params=dict(request.query_params))


@app.post("/api/admin/grants")
async def admin_create_grant(request: Request, authorization: str | None = Header(default=None)):
    token, denied = await _admin_guard(authorization)
    if denied:
        return denied
    payload = await _json_body(request)
    if payload is None:
        return JSONResponse(status_code=400, content=envelope(None, "请求体必须是 JSON 对象", 400))
    return await _admin_proxy("POST", "/api/admin/grants", payload, token)


@app.delete("/api/admin/grants/{grant_id}")
async def admin_revoke_grant(grant_id: int, authorization: str | None = Header(default=None)):
    token, denied = await _admin_guard(authorization)
    if denied:
        return denied
    return await _admin_proxy("DELETE", f"/api/admin/grants/{grant_id}", None, token)


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
