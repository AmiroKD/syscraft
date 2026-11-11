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
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from syscraft.agent.llm import LLMError, chat_with_tools
from syscraft.agent.tools import TOOLS_SPEC, ToolBox, ToolInputError
from syscraft.conversation.context import llm_history
from syscraft.requirement.model_catalog import EntityInfo, ModelCatalog
from syscraft.tools.spring_client import SpringClient, SpringError

# 工具循环上限。防模型反复查询兜圈：4 轮足够完成「查→统计→补查」的合理链路
MAX_TOOL_ROUNDS = 4

# 表格落库的行数上限。历史重开要能看见表格，但没必要把 200 行原样存进
# chat_message——前 50 行足够还原观感，合计条数仍然准确。
MAX_HISTORY_ROWS = 50

# 兑底卡片同款解析：从工具报错里提取「用户想要但不存在的实体名」（需求池上报用）。
# name 用 !r 拼，含引号时会切成双引号，两种都要接
_WANTED_ENTITY_RE = re.compile(r"实体 ['\"](.+?)['\"] 不存在")

# 指代词表（设计 3.2 的确定性解析）。命中且句子里没有显式实体名时才启用槽位
PRONOUN_ENTITY = ("它", "那张表", "刚才那张表", "这个表", "该表")
PRONOUN_RECORD = ("那条", "这条", "刚才那条", "刚才那条记录")

CONFIRM_WORDS = {"确认", "确定", "是的", "是", "对", "好的", "ok", "OK", "Ok", "执行", "删除吧", "删吧"}

# 取消待确认操作。只在真有待确认状态时短路——否则「取消的报名记录」这种
# 正常问句会被误吞。
CANCEL_WORDS = {"取消", "不用了", "算了", "放弃", "别删了", "不要了"}


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
    table_saved: bool         # 本轮最终表格是否已落库（多轮查询只留最后一张）
    last_tool_error: str | None
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
            "table_saved": False, "last_tool_error": None,
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

        # 工具轮次耗尽 / 模型空回复的兜底：已经查到的表格不能「有数据没结论」，
        # 用户看到的必须是「共几条 + 结果在下方」而不是一片空白（实测：模型连续
        # 重试到第 4 轮时图直接走到 respond，final.reply 是 None）。
        if not (final.get("reply") or "").strip():
            final["table_saved"] = self._persist_table(final)
            reply = self._fallback_reply(final)
            final["reply"] = reply
            final["session"].append("assistant", "text", reply)
            if on_event:
                on_event({"type": "text", "text": reply})
        return final

    # ---------------------------------------------------------------- 节点

    async def _prepare(self, state: AgentState) -> dict:
        message = (state["user_message"] or "").strip().strip("。，！!？?、, ")
        session = state["session"]
        events: list[dict] = []

        # ① 确认/取消词短路：不进 LLM——它没有实体与条件，只是「同意/放弃上一条预告」。
        #    令牌核销只有这一条路，模型不在环里（tools.py 的结构性约束）。
        #    只有真有待确认操作才短路：用户正常聊天说「好的」不该被硬回
        #    「没有待确认的操作」（实测体验：确认词误触会打断正常对话）
        short = message.lower()
        pending = (session.get_slot("pending_confirm")
                   if short in CONFIRM_WORDS or short in CANCEL_WORDS else None)
        if short in CANCEL_WORDS and pending is not None:
            session.append("user", "text", message)
            session.set_slot("pending_confirm", None)
            reply = "已取消本次删除，未执行任何操作。"
            session.append("assistant", "text", reply)
            events.append({"type": "text", "text": reply})
            return {"user_message": message, "reply": reply, "stop": True, "events": events}
        if short in CONFIRM_WORDS and pending is not None:
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
        if short in CONFIRM_WORDS and pending is None:
            session.set_slot("pending_confirm", None)  # 确认词但无预告：清掉可能的空槽位，正常进 LLM

        # ② 指代消解（设计 3.2，确定性规则优先于模型发挥）
        rewritten = message
        explicit = self._catalog.by_mention(message)
        if explicit is None and any(p in message for p in PRONOUN_ENTITY):
            last = session.get_slot("lastEntity") if session else None
            if not last:
                display_names = [e.display_name for e in self._catalog.all()]
                reply = ("你指的是哪张表？目前系统里有："
                         + "、".join(display_names) + "。")
                # 追问也发结构化事件：前端渲染成可点选的追问卡，
                # 点一下等于替用户把那句话发出来，不用照着文案手打
                content = _ask_content(
                    "entity", reply,
                    options=[{"label": name, "value": f"查一下{name}"}
                             for name in display_names])
                session.append("user", "text", message)
                session.append("assistant", "ask", content)
                events.append({"type": "ask", **content})
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
                content = _ask_content(
                    "record", reply,
                    options=[{"label": f"id={rid}", "value": f"我要操作 id={rid} 的这条记录"}
                             for rid in record_ids[:8]])
                session.append("user", "text", message)
                session.append("assistant", "ask", content)
                events.append({"type": "ask", **content})
                return {"user_message": message, "reply": reply, "stop": True, "events": events}
            else:
                reply = "我还不知道你指的是哪条记录，先查一下再说？"
                content = _ask_content("record", reply)
                session.append("user", "text", message)
                session.append("assistant", "ask", content)
                events.append({"type": "ask", **content})
                return {"user_message": message, "reply": reply, "stop": True, "events": events}

        # ③ 组装 LLM 消息：先取历史（不含本轮），再落盘本轮用户消息。
        #    只读最近 60 条：表格消息现在带行数据，读全量没有必要——12 轮原文
        #    之外都会被压成摘要，再往前的记录对模型没有增量信息。
        history = llm_history(session.messages(limit=60)) if session else []
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
            saved = self._persist_table(state)
            state["session"].append("assistant", "text", reply)
            return {"reply": reply, "stop": True, "table_saved": saved,
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
        reply = (result["content"] or "").strip()
        # 表格先落库再落文本：历史重开时的顺序必须是「用户问 → 表格 → 结论」。
        # 多轮查询只保留最终表格，中间试错的表不再堆进历史。
        saved = self._persist_table(state)
        if not reply:
            # 空回复交给 run() 的兜底：有表格就报条数，没有就请用户重说
            return {"reply": None, "tool_calls": [], "stop": True,
                    "table_saved": saved, "events": []}
        state["session"].append("assistant", "text", reply)
        return {"reply": reply, "tool_calls": [], "stop": True,
                "table_saved": saved, "events": [{"type": "text", "text": reply}]}

    async def _tools_node(self, state: AgentState) -> dict:
        session = state["session"]
        toolbox = ToolBox(self._client, self._catalog, state["token"])
        events: list[dict] = []
        rounds = state.get("tool_rounds", 0) + 1
        stop = False
        table = state.get("table")
        reply = state.get("reply")
        saved = state.get("table_saved", False)
        last_error = state.get("last_tool_error")

        for call in state.get("tool_calls", []):
            name = call.get("name", "")
            args = call.get("arguments") or {}
            started = time.perf_counter()
            try:
                if call.get("arguments_error"):
                    # 参数 JSON 坏了也要以「工具结果」回灌，模型下一轮能自己改对；
                    # 直接抛异常会让整轮对话消失（且用户只看到一句「处理失败」）
                    raise ToolInputError(call["arguments_error"])
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
                entity = self._catalog.resolve(str(args.get("entity", "")))
                result = {"ok": False, "error": _spring_message(error, entity)}
            except Exception as error:  # noqa: BLE001 单个工具出错不能击穿整轮对话
                result = {"ok": False,
                          "error": f"工具执行异常：{type(error).__name__}: {error}"}

            # 本调用产出的表格事件先算好、后发：界面上的顺序必须是
            # 「调了什么 → 拿到什么表 → 得到什么结论」，而且要等结果定稿
            # （比如实体不在线版本里，要改判成失败）再发轨迹。
            pending_events: list[dict] = []

            if name == "query_records" and result.get("ok"):
                entity = self._catalog.resolve(result["entity"])
                if entity is None:
                    result = {"ok": False, "error": f"实体 {result['entity']!r} 不在在线版本里"}
                else:
                    rows = result.pop("__rows__", [])
                    table = {"columns": [{"key": f.column, "title": _label(f), "type": f.type}
                                         for f in entity.fields],
                             "rows": rows, "total": result.get("total", 0),
                             "entity": entity.entity, "display": entity.display_name,
                             "toolCall": {"name": name, "args": args}}
                    pending_events.append({"type": "table", "table": table,
                                           "replace": True})  # 同一轮只展示最新一张表

            if name == "aggregate_records" and result.get("ok"):
                # 聚合结果过去只有一行「共 N 条」的正文，用户看不到分组明细。
                # 这里补一张表（带 chart 元数据），前端才能既给表又给图。
                aggregate = self._aggregate_table(result, args)
                if aggregate is not None:
                    table = aggregate
                    pending_events.append({"type": "table", "table": table, "replace": True})

            # 工具轨迹一条事件说完：叫什么、传了什么、成没成、花了多久。
            # 前端据此画可折叠的调用轨迹（《前端交互设计》11.3），
            # 而不是把它塞进助手正文——正文被调用日志埋掉是最难读的界面。
            events.append({
                "type": "tool_call", "name": name, "args": _brief_args(args),
                "ok": bool(result.get("ok")),
                "error": (str(result.get("error") or "")[:200]
                          if not result.get("ok") else ""),
                "ms": round((time.perf_counter() - started) * 1000, 1),
            })
            events.extend(pending_events)

            if result.get("status") == "need_confirmation":
                # 预告文案来自产物侧，平台不加工（与既有对话层同一纪律）
                summary = result.get("summary", "")
                reply = summary + "。确认请回复「确认」。"
                # 确认前查过的表格也要落库，否则历史里只剩一句「将删除」没有依据
                saved = self._persist_table({**state, "table": table,
                                             "table_saved": saved})
                session.append("assistant", "confirm", {"summary": summary})
                events.append({"type": "confirm", "summary": summary, "text": reply})
                stop = True

            tool_payload = {k: v for k, v in result.items() if k != "__rows__"}
            if not result.get("ok"):
                error_text = str(result.get("error") or result.get("message") or "调用失败")
                last_error = error_text
                tool_payload["hint"] = "请根据错误修正参数后重试；无法修正就向用户说明原因。"
            state["llm_messages"].append({
                "role": "tool", "tool_call_id": call["id"],
                "content": json.dumps(tool_payload, ensure_ascii=False)})

        return {"llm_messages": state["llm_messages"], "tool_rounds": rounds,
                "events": events, "stop": stop, "table": table,
                "tool_calls": [], "reply": reply,
                "table_saved": saved, "last_tool_error": last_error}

    def _persist_table(self, state: dict) -> bool:
        """把本轮最终表格写进会话历史（只写一次、只写前 50 行）。

        表格是「结论」的一部分：历史里只留「共 N 条」会让重开的对话看不出
        当时到底查到了什么（也是用户反馈「历史显示异常」的直接原因）。
        kind/chart 也要一起落库，否则重开历史只能看表、画不出图。
        """
        table = state.get("table")
        if not table or state.get("table_saved"):
            return bool(state.get("table_saved"))
        rows = table.get("rows") or []
        kept = rows[:MAX_HISTORY_ROWS]
        content = {"entity": table.get("entity"), "display": table.get("display"),
                   "columns": table.get("columns") or [], "rows": kept,
                   "total": table.get("total", len(rows)), "displayed": len(kept)}
        if table.get("kind"):
            content["kind"] = table["kind"]
        if table.get("chart"):
            content["chart"] = table["chart"]
        state["session"].append("assistant", "table", content,
                                tool_call=table.get("toolCall"))
        state["table_saved"] = True
        return True

    def _aggregate_table(self, result: dict, args: dict) -> dict | None:
        """把聚合结果包成一张表：分组列用实体的中文语义，度量列用别名。

        这样同一份数据在界面上有两种看法——表格看精确值，图表看分布。
        列名从结果行反推而不是照抄参数：别名归一化发生在 ToolBox 里，
        这里的原始 args 已经对不上了。
        """
        rows = result.get("rows") or []
        if not isinstance(rows, list) or not rows:
            return None
        entity = self._catalog.resolve(str(result.get("entity", "")))

        keys: list[str] = []
        for row in rows:
            if isinstance(row, dict):
                keys.extend(key for key in row if key not in keys)

        columns, dimensions, measures = [], [], []
        for key in keys:
            field = entity.field(key) if entity is not None else None
            if field is not None:
                columns.append({"key": key, "title": _label(field), "type": field.type})
                numeric = _is_numeric_type(field.type)
            else:
                sample = next((row.get(key) for row in rows
                               if isinstance(row, dict) and row.get(key) is not None), None)
                columns.append({"key": key, "title": key, "type": _guess_type(sample)})
                numeric = _is_numeric_type(_guess_type(sample))
            (measures if numeric else dimensions).append(key)

        if not measures:
            return None   # 只有分组没有度量，画不出图，也没必要单独成表

        return {
            "entity": entity.entity if entity is not None else result.get("entity"),
            "display": (f"{entity.display_name} · 分组统计"
                        if entity is not None else "分组统计"),
            "columns": columns, "rows": rows,
            "total": len(rows), "displayed": len(rows),
            "kind": "aggregate",
            "chart": {"dimensions": dimensions, "measures": measures},
            "toolCall": {"name": "aggregate_records", "args": args},
        }

    @staticmethod
    def _fallback_reply(state: dict) -> str:
        """没有最终回复时的确定性兜底：有表报条数，有错说原因，都没有请重说。"""
        table = state.get("table") or {}
        display = table.get("display") or table.get("entity") or "数据"
        total = table.get("total")
        if total is not None:
            if total:
                return (f"已查询到 {total} 条{display}，结果见上方表格。"
                        "需要继续筛选、排序或统计，直接告诉我条件即可。")
            return f"没有查询到符合条件的{display}，可以换个条件再试。"
        error = state.get("last_tool_error")
        if error:
            return f"这次操作没有成功：{error}。可以补充更具体的条件，或换个说法再试。"
        return "我这边没有生成有效回复，请再描述一次需求。"

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


def _ask_content(kind: str, text: str, options: list | None = None,
                 items: list | None = None) -> dict:
    """追问消息的结构化载荷：text 是给模型与历史看的原话，
    options（点选即发送）与 items（待填问题）是给界面渲染的。"""
    return {"kind": kind, "text": text,
            "options": options or [], "items": items or []}


def _brief_args(value, depth: int = 0):
    """工具参数瘦身：结构留着（前端要按键名展示），字符串截断到 120 字。

    不整体转成字符串的原因：前端按 `filter` / `groupBy` / `metrics` 的结构
    渲染标签，压成字符串就只剩一坨 JSON 了。SSE 每条事件都走一遍网络，
    长文本必须在这里截住。
    """
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        return {str(key): _brief_args(item, depth + 1)
                for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_brief_args(item, depth + 1) for item in list(value)[:8]]
    if isinstance(value, str):
        return value[:120]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:120]


def _is_numeric_type(sql_type: str | None) -> bool:
    """SQL 类型 → 能不能当度量。与前端 lib/columns.js 保持同一套判定口径。"""
    base = str(sql_type or "").split("(")[0].strip().upper()
    return base in {"TINYINT", "SMALLINT", "MEDIUMINT", "INT", "INTEGER", "BIGINT",
                    "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL", "NUMBER",
                    "BIT", "MONEY"}


def _guess_type(sample) -> str:
    """聚合别名列没有元数据：按首个非空值猜一个类型，够前端对齐与格式化用。"""
    if isinstance(sample, bool):
        return "TINYINT"
    if isinstance(sample, int):
        return "BIGINT"
    if isinstance(sample, float):
        return "DECIMAL"
    return "VARCHAR"


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
