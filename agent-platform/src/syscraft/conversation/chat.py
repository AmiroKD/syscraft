"""编排层：一句话 → 意图 → 调产物接口 → 表格 + 一句人话回复。

这里不写业务规则，也不拼 SQL：查询条件由 intent 模块产出，
数据由产物侧的 /query 端点返回，平台只负责把 rows 变成能看的表格。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

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


class ChatService:
    def __init__(self, client: SpringClient, resolver: IntentResolver):
        self._client = client
        self._resolver = resolver

    async def handle(self, message: str, token: str) -> ChatReply:
        try:
            intent = self._resolver.resolve(message)
        except IntentError as error:
            return ChatReply(reply=str(error))

        try:
            data = await self._client.query(intent.entity.entity, build_query(intent), token)
        except SpringError as error:
            return ChatReply(reply=explain(error, intent.entity))

        table = to_table(intent.entity, data)
        return ChatReply(reply=summarize(intent.entity, table), table=table)


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
