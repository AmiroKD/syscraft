"""业务模式 Agent：LangGraph 状态机编排工具循环。

图形状（论文 4.7 的素材就画这张）：

    START → prepare ─┬→ agent ⇄ tools ─┬→ END
                     └─────────────────┘（stop 短路：确认/追问/预告）

节点职责：
- prepare   确认词短路、指代消解（确定性规则，设计 3.2：槽位为空就追问，不靠模型猜）、
            组装 LLM 消息（系统提示 + 裁剪后的历史 + 本轮用户消息）
- agent     调 LLM（原生 tools）；模型要么发工具调用，要么给最终回复
- tools     执行工具（白名单校验在 ToolBox），更新槽位，产表格/预告事件
- respond   汇合出口

关于检查点（《技术栈》3.3 说 LangGraph + SqliteSaver）：本图是**单轮内**的状态机，
一次请求内跑完、没有图内 interrupt——确认流是「消息级」的人的回路（用户下一条消息
带确认词），不是 LangGraph 的 interrupt/resume。跨请求的恢复由会话层 SQLite 承担
（《会话与上下文策略》1.1 每会话一文件），LangGraph 的 checkpointer 留给生成模式
那种真正需要挂起恢复的图。这是如实的取舍，不是遗漏。
"""

from __future__ import annotations

import json
import operator
import re
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from syscraft.agent.llm import LLMError, chat_with_tools
from syscraft.agent.tools import TOOLS_SPEC, ToolBox, ToolInputError
from syscraft.conversation.context import llm_history
from syscraft.requirement.model_catalog import EntityInfo, ModelCatalog
from syscraft.tools.spring_client import SpringClient, SpringError

# 工具循环上限。防模型反复查询兜圈：4 轮足够完成「查→统计→补查」的合理链路
MAX_TOOL_ROUNDS = 4

# 兑底卡片同款解析：从工具报错里提取「用户想要但不存在的实体名」（需求池上报用）。
# name 用 !r 拼，含引号时会切成双引号，两种都要接
_WANTED_ENTITY_RE = re.compile(r"实体 ['\"](.+?)['\"] 不存在")

# 指代词表（设计 3.2 的确定性解析）。命中且句子里没有显式实体名时才启用槽位
PRONOUN_ENTITY = ("它", "那张表", "刚才那张表", "这个表", "该表")
PRONOUN_RECORD = ("那条", "这条", "刚才那条", "刚才那条记录")

CONFIRM_WORDS = {"确认", "确定", "是的", "是", "对", "好的", "ok", "OK", "Ok", "执行", "删除吧", "删吧"}


class AgentState(TypedDict, total=False):
    user_message: str
    token: str
    session: Any              # 会话句柄（读写消息与槽位）；不参与 LLM 消息
    # events 用 add reducer：多节点都产事件，默认通道行为是替换——
    # tools 节点的表格事件会被 agent 节点的文本事件覆盖（实测踩过）
    events: Annotated[list[dict], operator.add]
    llm_messages: list[dict]
    tool_calls: list[dict]    # 待执行的工具调用
    tool_rounds: int
    reply: str | None
    table: dict | None
    stop: bool


def build_system_prompt(cat: ModelCatalog, slots: dict) -> str:
    entities = []
    for info in cat.all():
        fields = "；".join(f"{f.column}({f.semantic},{f.type})" for f in info.fields)
        entities.append(f"- {info.display_name}（表名 {info.entity}）：{fields}")
    slot_lines = []
    if slots.get("lastEntity"):
        slot_lines.append(f"最近操作的实体：{slots['lastEntity'].get('display')}（{slots['lastEntity'].get('entity')}）")
    if slots.get("lastResultIds") is not None:
        slot_lines.append(f"最近一次查询结果的主键：{slots['lastResultIds'][:20]}")
    return (
        "你是业务系统的数据助手，通过工具读写用户的业务数据。规则：\n"
        "1. 实体与字段只能用下面的清单，不要编造：\n" + "\n".join(entities) + "\n"
        + ("2. 会话上下文：" + "；".join(slot_lines) + "\n" if slot_lines
           else "2. 会话刚开始，没有历史上下文。\n")
        + "3. 用户说「它/那张表」指最近的实体，说「那条」指最近查询结果里的记录；上下文不足就如实问，不要猜。\n"
          "4. 查询后用一句话汇报（共几条、关键统计），不要把表格数据复述一遍——用户界面上已经有表格。\n"
          "5. 删除只调用 delete_records 生成预告，用户确认后系统会自动执行，你不需要也不能执行删除。\n"
          "6. 最后一次回复要用简体中文、面向用户，说清做了什么、结果如何。"
    )


class BusinessAgent:
    """一次对话请求 = 跑一遍图。实例持有共享依赖（client/catalog），可跨请求复用。

    catalog 用 provider 而不是实例：对话内生成新实体后 cache_clear()，
    这里必须拿到新目录——握着启动时的旧引用会让新系统对本 Agent 隐身（实测踩过）。"""

    def __init__(self, client: SpringClient, cat=None):
        from syscraft.requirement.model_catalog import catalog as default_catalog
        self._client = client
        if cat is None:
            # online_only：Agent 模式的工具白名单只认在线版本的实体（r5 留存项修复）
            self._catalog_provider = lambda: default_catalog(online_only=True)
        elif callable(cat):
            self._catalog_provider = cat  # 测试注入
        else:
            self._catalog_provider = lambda: cat  # 兼容直接传实例（测试用）
        self._graph = self._build()

    @property
    def _catalog(self) -> ModelCatalog:
        return self._catalog_provider()

    def _build(self):
        graph = StateGraph(AgentState)
        graph.add_node("prepare", self._prepare)
        graph.add_node("agent", self._agent_node)
        graph.add_node("tools", self._tools_node)
        graph.add_node("respond", self._respond)
        graph.add_edge(START, "prepare")
        graph.add_conditional_edges("prepare", lambda s: "respond" if s.get("stop") else "agent")
        graph.add_conditional_edges(
            "agent", lambda s: "tools" if s.get("tool_calls") and not s.get("stop") else "respond")
        graph.add_conditional_edges(
            "tools", lambda s: "agent" if not s.get("stop") and s.get("tool_rounds", 0) < MAX_TOOL_ROUNDS else "respond")
        graph.add_edge("respond", END)
        return graph.compile()

    @staticmethod
    def _respond(state: AgentState) -> dict:
        """汇合出口。必须返回空更新：返回整个 state 会把已累积的 events
        经 add reducer 再加一遍，所有事件双投（实测踩过）。"""
        return {}

    async def run(self, message: str, token: str, session, *, on_event=None) -> dict:
        """跑一轮对话。on_event(event_dict) 用于 SSE 推送；返回最终状态。"""
        state: AgentState = {
            "user_message": message, "token": token, "session": session,
            "events": [], "llm_messages": [], "tool_rounds": 0,
            "reply": None, "table": None, "stop": False,
        }
        # stream_mode="updates"：每个节点跑完推一次增量；
        # 最终状态由增量自行合并（节点返回的都是部分键）
        final: dict = dict(state)
        async for chunk in self._graph.astream(state, stream_mode="updates"):
            for node_update in chunk.values():
                if not node_update:
                    continue  # respond 返回的空更新会被归一成 None
                for key, value in node_update.items():
                    if key == "events":
                        # 与 reducer 对齐：事件增量合并，不替换
                        final["events"] = final.get("events", []) + value
                    else:
                        final[key] = value
                for event in node_update.get("events", []):
                    if on_event:
                        on_event(event)
        return final

    # ---------------------------------------------------------------- 节点

    async def _prepare(self, state: AgentState) -> dict:
        message = (state["user_message"] or "").strip().strip("。，！!？?、, ")
        session = state["session"]
        events: list[dict] = []

        # ① 确认词短路：不进 LLM——它没有实体与条件，只是「同意上一条预告」。
        #    令牌核销只有这一条路，模型不在环里（tools.py 的结构性约束）。
        #    只有真有待确认操作才短路：用户正常聊天说「好的」不该被硬回
        #    「没有待确认的操作」（实测体验：确认词误触会打断正常对话）
        pending = session.get_slot("pending_confirm") if message.lower() in CONFIRM_WORDS else None
        if message.lower() in CONFIRM_WORDS and pending is not None:
            session.append("user", "text", message)
            session.set_slot("pending_confirm", None)
            if self._expired(pending):
                reply = "待确认的操作已超时（令牌 5 分钟有效），请重新发起。"
            else:
                try:
                    rows = await self._client.delete(pending["entity"], pending["ids"],
                                                     pending["token"], state["token"])
                    reply = f"已删除 {rows} 条。"
                except SpringError as error:
                    reply = ("确认令牌已失效（过期或已被使用），请重新发起删除。"
                             if error.status == 409 else f"执行失败：{error.message}")
            session.append("assistant", "text", reply)
            events.append({"type": "text", "text": reply})
            return {"user_message": message, "reply": reply, "stop": True, "events": events}
        if message.lower() in CONFIRM_WORDS and pending is None:
            session.set_slot("pending_confirm", None)  # 确认词但无预告：清掉可能的空槽位，正常进 LLM

        # ② 指代消解（设计 3.2，确定性规则优先于模型发挥）
        rewritten = message
        explicit = self._catalog.by_mention(message)
        if explicit is None and any(p in message for p in PRONOUN_ENTITY):
            last = session.get_slot("lastEntity") if session else None
            if not last:
                reply = ("你指的是哪张表？目前系统里有："
                         + "、".join(e.display_name for e in self._catalog.all()) + "。")
                session.append("user", "text", message)
                session.append("assistant", "text", reply)
                events.append({"type": "text", "text": reply})
                return {"user_message": message, "reply": reply, "stop": True, "events": events}
            for pronoun in PRONOUN_ENTITY:
                rewritten = rewritten.replace(pronoun, last["display"])

        if session and any(p in message for p in PRONOUN_RECORD):
            record_ids = session.get_slot("lastResultIds") or []
            last = session.get_slot("lastEntity") or {}
            if len(record_ids) == 1:
                rewritten += f"（指代解析：{last.get('display', '')} 中 id={record_ids[0]} 的那条记录）"
            elif len(record_ids) > 1:
                reply = (f"最近一次查询有 {len(record_ids)} 条记录（id: {record_ids}），"
                         "请说明要操作哪一条。")
                session.append("user", "text", message)
                session.append("assistant", "text", reply)
                events.append({"type": "text", "text": reply})
                return {"user_message": message, "reply": reply, "stop": True, "events": events}
            else:
                reply = "我还不知道你指的是哪条记录，先查一下再说？"
                session.append("user", "text", message)
                session.append("assistant", "text", reply)
                events.append({"type": "text", "text": reply})
                return {"user_message": message, "reply": reply, "stop": True, "events": events}

        # ③ 组装 LLM 消息：先取历史（不含本轮），再落盘本轮用户消息
        history = llm_history(session.messages()) if session else []
        session.append("user", "text", message)
        slots = {"lastEntity": session.get_slot("lastEntity"),
                 "lastResultIds": session.get_slot("lastResultIds")}
        messages = [{"role": "system", "content": build_system_prompt(self._catalog, slots)}]
        messages += history
        messages.append({"role": "user", "content": rewritten})
        return {"user_message": rewritten, "llm_messages": messages,
                "events": events, "stop": False}

    async def _agent_node(self, state: AgentState) -> dict:
        try:
            result = await chat_with_tools(state["llm_messages"], TOOLS_SPEC)
        except LLMError as error:
            reply = f"模型服务暂时不可用：{error}"
            state["session"].append("assistant", "text", reply)
            return {"reply": reply, "stop": True,
                    "events": [{"type": "error", "message": reply}]}
        if result["tool_calls"]:
            # OpenAI 协议要求：带 tool_calls 的 assistant 消息必须在下一轮请求里原样带上
            state["llm_messages"].append({
                "role": "assistant", "content": result["content"] or "",
                "tool_calls": [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"],
                                             "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                               for c in result["tool_calls"]]})
            return {"llm_messages": state["llm_messages"],
                    "tool_calls": result["tool_calls"], "stop": False}
        reply = (result["content"] or "（模型没有返回内容）").strip()
        state["session"].append("assistant", "text", reply)
        return {"reply": reply, "tool_calls": [], "stop": True,
                "events": [{"type": "text", "text": reply}]}

    async def _tools_node(self, state: AgentState) -> dict:
        session = state["session"]
        toolbox = ToolBox(self._client, self._catalog, state["token"])
        events: list[dict] = []
        rounds = state.get("tool_rounds", 0) + 1
        stop = False
        table = state.get("table")
        reply = state.get("reply")

        for call in state.get("tool_calls", []):
            name, args = call["name"], call["arguments"]
            events.append({"type": "thought", "text": f"调用 {name}"})
            try:
                result = await toolbox.call(name, args, session)
            except ToolInputError as error:
                result = {"ok": False, "error": str(error)}
                # 能力边界兜底（P1-1）：实体不存在是「系统没有这个能力」，措辞不交给
                # 模型自由发挥——直接发结构化能力卡片，前端确定性渲染 + 生成引导
                if "可用实体" in str(error):
                    events.append({
                        "type": "capability",
                        "available": [{"entity": e.entity, "display": e.display_name}
                                      for e in self._catalog.all()],
                        "hint": "系统里还没有这个能力。可以联系管理员描述需求生成新系统。",
                    })
                    # 需求池上报（底座扩展 v0.1）：兑底卡片解释能力边界的同时把「用户要了
                    # 但系统没有」落到 adm_feature_request，管理员可采纳后触发生成。
                    # 上报是副线：任何失败都吞掉，绝不影响主对话链路
                    await self._report_feature_request(session, str(error), state)
            except SpringError as error:
                entity = self._catalog.by_name(str(args.get("entity", "")))
                result = {"ok": False, "error": _spring_message(error, entity)}

            if name == "query_records" and result.get("ok"):
                entity = self._catalog.by_name(result["entity"])
                rows = result.pop("__rows__", [])
                table = {"columns": [{"key": f.column, "title": _label(f), "type": f.type}
                                     for f in entity.fields],
                         "rows": rows, "total": result.get("total", 0)}
                session.append("assistant", "table", {"entity": entity.entity, "total": table["total"],
                                                      "displayed": len(rows)},
                               tool_call={"name": name, "args": args})
                events.append({"type": "table", "table": table})

            if result.get("status") == "need_confirmation":
                # 预告文案来自产物侧，平台不加工（与既有对话层同一纪律）
                summary = result.get("summary", "")
                reply = summary + "。确认请回复「确认」。"
                session.append("assistant", "confirm", {"summary": summary})
                events.append({"type": "confirm", "summary": summary, "text": reply})
                stop = True

            state["llm_messages"].append({
                "role": "tool", "tool_call_id": call["id"],
                "content": json.dumps({k: v for k, v in result.items() if k != "__rows__"},
                                      ensure_ascii=False)})
            events.append({"type": "tool_result", "name": name, "ok": bool(result.get("ok"))})

        return {"llm_messages": state["llm_messages"], "tool_rounds": rounds,
                "events": events, "stop": stop, "table": table,
                "tool_calls": [], "reply": reply}

    async def _report_feature_request(self, session, error_text: str, state: AgentState) -> None:
        """把「用户要了但系统没有」上报需求池（底座扩展 v0.1）。

        走平台 API 而非直连 adm——权限 6 节的物理不可达约束不因新功能破例。
        副线纪律：整体 try/except，任何失败（网络/权限/频控）都静默放弃，
        对话主链路对此无感知；repeat 触发由平台侧频控兜底。
        """
        try:
            match = _WANTED_ENTITY_RE.search(error_text)
            raw_text = _last_user_text(session) or error_text
            await self._client.report_feature_request(raw_text, match.group(1) if match else None,
                                                      state["token"])
        except Exception:  # noqa: BLE001 - 上报失败不影响对话
            pass

    @staticmethod
    def _expired(pending: dict) -> bool:
        from datetime import datetime
        try:
            return datetime.fromisoformat(pending["expiresAt"]) < datetime.now()
        except (KeyError, ValueError):
            return True


def _label(field) -> str:
    """表格列标题。语义描述常常是一整句（「活动结束时间，可为空表示未设定」），
    直接当表头会把列宽撑爆；取首个分句并限长，完整语义仍留在 LLM prompt 里。"""
    text = str(getattr(field, "semantic", "") or "").strip()
    for separator in ("，", ",", "；", ";", "（", "(", "。"):
        text = text.split(separator)[0]
    return (text[:12] or field.column).strip()


def _last_user_text(session) -> str | None:
    """最近一条用户消息的文本（需求池的原话字段）。取不到就返回 None，由上报侧用错误文案兜底"""
    try:
        for message in reversed(session.messages(limit=20)):
            if message["role"] == "user" and message["type"] == "text":
                content = message["content"]
                text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                return text[:500]
    except Exception:  # noqa: BLE001 - 会话读取失败不影响上报主流程
        pass
    return None


def _spring_message(error: SpringError, entity: EntityInfo | None) -> str:
    label = entity.display_name if entity else "该数据"
    if error.status == 401:
        return "登录已失效，请重新登录。"
    if error.status == 403:
        return f"当前账号没有对「{label}」执行此操作的权限。"
    if error.status == 404:
        return f"系统里没有「{label}」的接口，可能版本不一致。"
    return f"操作失败：{error.message}"
