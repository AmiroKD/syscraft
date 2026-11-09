"""编排层：一句话 → 意图 → 调产物接口 → 表格 + 一句人话回复。

这里不写业务规则，也不拼 SQL：查询条件由 intent 模块产出，
数据由产物侧的 /query 端点返回，平台只负责把 rows 变成能看的表格。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from syscraft.conversation.session import PendingConfirm, SessionStore
from syscraft.requirement.intent import Intent, IntentError, IntentResolver
from syscraft.requirement.model_catalog import EntityInfo
from syscraft.tools.spring_client import SpringClient, SpringError


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    type: str


@dataclass(frozen=True)
class Table:
    columns: tuple[Column, ...]
    rows: list[dict[str, Any]]
    total: int
    page: int
    size: int
    truncated: bool


@dataclass(frozen=True)
class ChatReply:
    reply: str
    table: Table | None = None


# 「确认」这类话不进意图解析：它没有实体与条件，只有「是否同意上一条预告」
CONFIRM_WORDS = {"确认", "确定", "是的", "是", "对", "好的", "ok", "OK", "Ok", "执行", "删除吧", "删吧"}

# 预览匹配上限。超过它就拒绝并请用户缩小范围：删 500 条的预告文案谁都不会细看，
# 而确认弹窗的意义恰恰是让人看清影响面
MAX_DELETE_SCAN = 200

# 平台侧给待确认状态的余量：令牌服务端 5 分钟过期，这里留 10 秒缓冲，
# 避免「确认」发出去的瞬间令牌刚好过期，收到一句数据库味的 409
CONFIRM_TTL = timedelta(minutes=4, seconds=50)


def normalize(message: str) -> str:
    return (message or "").strip().strip("。，！!？?、, ")


class ChatService:
    def __init__(self, client: SpringClient, resolver: IntentResolver,
                 sessions: SessionStore | None = None):
        self._client = client
        self._resolver = resolver
        self._sessions = sessions or SessionStore()

    async def handle(self, message: str, token: str, session_id: str | None = None) -> ChatReply:
        text = normalize(message)
        if text.lower() in CONFIRM_WORDS:
            pending = self._sessions.pop(session_id)
            if pending is None:
                return ChatReply("当前没有待确认的操作。")
            return await self._execute_confirm(pending, token)

        try:
            intent = self._resolver.resolve(message)
        except IntentError as error:
            return ChatReply(reply=str(error))

        if intent.op == "DELETE":
            return await self._begin_delete(intent, token, session_id)
        return await self._run_query(intent, token)

    async def _run_query(self, intent: Intent, token: str) -> ChatReply:
        try:
            data = await self._client.query(intent.entity.entity, build_query(intent), token)
        except SpringError as error:
            return ChatReply(reply=explain(error, intent.entity))

        table = to_table(intent.entity, data)
        return ChatReply(reply=summarize(intent.entity, table), table=table)

    async def _begin_delete(self, intent: Intent, token: str, session_id: str | None) -> ChatReply:
        query = build_query(intent)
        query["size"] = MAX_DELETE_SCAN
        try:
            data = await self._client.query(intent.entity.entity, query, token)
        except SpringError as error:
            return ChatReply(reply=explain(error, intent.entity))

        total = data.get("total", 0)
        if total == 0:
            return ChatReply(f"没有找到符合条件的{intent.entity.display_name}。")
        if total > MAX_DELETE_SCAN:
            return ChatReply(
                f"符合条件的{intent.entity.display_name}有 {total} 条，超出单次删除上限（{MAX_DELETE_SCAN} 条）。"
                "请缩小范围，例如按订单号指定。")

        ids = tuple(sorted({row["id"] for row in data.get("rows", []) if row.get("id") is not None}))
        try:
            ticket = await self._client.preview_delete(intent.entity.entity, list(ids), token)
        except SpringError as error:
            return ChatReply(reply=explain(error, intent.entity))

        self._sessions.put(session_id, PendingConfirm(
            token=ticket["token"],
            entity=intent.entity.entity,
            ids=ids,
            summary=ticket["summary"],
            expires_at=datetime.now() + CONFIRM_TTL,
        ))
        # 预告文案来自产物侧（含实体中文名与影响行数），平台不加工——两个出处迟早说不一致
        return ChatReply(ticket["summary"] + "。确认请回复「确认」。")

    async def _execute_confirm(self, pending: PendingConfirm, token: str) -> ChatReply:
        try:
            rows = await self._client.delete(pending.entity, list(pending.ids), pending.token, token)
        except SpringError as error:
            if error.status == 409:
                return ChatReply("确认令牌已失效（过期或已被使用），请重新发起删除。")
            entity = self._resolver.catalog.by_name(pending.entity)
            return ChatReply(explain(error, entity) if entity else f"执行失败：{error.message}")
        return ChatReply(f"已删除 {rows} 条。")


def build_query(intent: Intent) -> dict[str, Any]:
    conditions = [
        {"field": c.column, "operator": c.operator, "value": c.value}
        for c in intent.conditions
    ]
    return {
        "filter": {"op": "AND", "conditions": conditions} if conditions else None,
        "orderBy": [{"field": field, "direction": direction} for field, direction in intent.order_by],
        "page": intent.page,
        "size": intent.size,
    }


def to_table(entity: EntityInfo, data: dict[str, Any]) -> Table:
    columns = tuple(Column(key=f.column, title=f.semantic, type=f.type) for f in entity.fields)
    return Table(
        columns=columns,
        rows=data.get("rows", []),
        total=data.get("total", 0),
        page=data.get("page", 1),
        size=data.get("size", 20),
        truncated=data.get("truncated", False),
    )


def summarize(entity: EntityInfo, table: Table) -> str:
    if table.total == 0:
        return f"没有查到{entity.display_name}。"

    # 只显示了一部分时必须说清合计是「本页」的，否则用户会拿它当全量合计
    partial = table.truncated or table.total > len(table.rows)
    parts = [f"共 {table.total} 条{entity.display_name}"]
    if partial:
        parts.append(f"已显示前 {len(table.rows)} 条")

    money = total_of(table, "amount")
    if money is not None:
        parts.append(f"{'本页金额合计' if partial else '金额合计'} {money:.2f}")
    return "，".join(parts) + "。"


def total_of(table: Table, column: str) -> float | None:
    """只在所有行都带这个数值列时才算合计，避免把「部分行为空」算成 0 误导用户。"""
    if not table.rows or any(row.get(column) is None for row in table.rows):
        return None
    try:
        return float(sum(Decimal(str(row[column])) for row in table.rows))
    except (InvalidOperation, TypeError, ValueError):
        return None


def explain(error: SpringError, entity: EntityInfo) -> str:
    if error.status == 401:
        return "登录已失效，请重新登录。"
    if error.status == 403:
        return f"当前账号没有查看「{entity.display_name}」的权限。"
    if error.status == 404:
        return f"产物侧没有「{entity.display_name}」的接口，可能是版本不一致。"
    return f"查询失败：{error.message}"
