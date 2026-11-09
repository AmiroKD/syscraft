"""数据组五工具（《智能体工具协议》2.1）：query/aggregate/insert/update/delete。

三条硬约束：
1. 实体、字段、操作符全部过确定性白名单（catalog），模型只能从清单里挑——
   字段幻觉在这里被拦下，而不是等 Spring 报 500；
2. 令牌透传：工具不解析权限，鉴权全在产物侧（协议 1 节第 3 条）；
3. **delete_records 在模型侧只有预告职责**。协议原文允许工具携带 confirmToken，
   实现收紧为：模型调 delete 只会触发预告并把待确认状态写进会话——
   令牌核销只发生在编排层的确认路径（用户回复「确认」）。
   模型永远无法直接执行删除，这是比「参数校验」更强的结构性保证。
"""

from __future__ import annotations

from typing import Any

from syscraft.conversation.context import tool_result_for_llm
from syscraft.requirement.model_catalog import EntityInfo, ModelCatalog
from syscraft.tools.spring_client import SpringClient

# 查询 DSL 允许的操作符（与《通用数据接口设计》一致）
OPERATORS = {"EQ", "NE", "GT", "GE", "LT", "LE", "LIKE", "IN", "BETWEEN", "IS_NULL"}

# 预告阶段允许的最大匹配行数（与既有对话层一致：删 500 条的预告没人会细看）
MAX_DELETE_SCAN = 200

TOOLS_SPEC: list[dict] = [
    {"type": "function", "function": {
        "name": "query_records",
        "description": "按条件查询业务数据。filter 可为 null（查全部）。返回 total 与 rows。",
        "parameters": {"type": "object", "properties": {
            "entity": {"type": "string", "description": "实体表名"},
            "filter": {"type": "object", "description": "过滤条件 {op, conditions:[{field,operator,value}]}，可为 null"},
            "page": {"type": "integer", "description": "页码，默认 1"},
            "size": {"type": "integer", "description": "每页条数，默认 20，最大 200"},
        }, "required": ["entity"]}}},
    {"type": "function", "function": {
        "name": "aggregate_records",
        "description": "分组统计。groupBy 为字段名数组；metrics 为 [{func: COUNT/SUM/AVG/MIN/MAX, field?, alias?}]。",
        "parameters": {"type": "object", "properties": {
            "entity": {"type": "string"},
            "groupBy": {"type": "array", "items": {"type": "string"}},
            "metrics": {"type": "array", "items": {"type": "object"}},
            "filter": {"type": "object"},
        }, "required": ["entity"]}}},
    {"type": "function", "function": {
        "name": "insert_records",
        "description": "新增一条记录。row 的键必须是实体字段名，返回新记录 id。",
        "parameters": {"type": "object", "properties": {
            "entity": {"type": "string"},
            "row": {"type": "object", "description": "字段名 → 值"},
        }, "required": ["entity", "row"]}}},
    {"type": "function", "function": {
        "name": "update_records",
        "description": "按 id 修改记录的部分字段。values 只放要改的字段。",
        "parameters": {"type": "object", "properties": {
            "entity": {"type": "string"},
            "id": {"type": "integer"},
            "values": {"type": "object"},
        }, "required": ["entity", "id", "values"]}}},
    {"type": "function", "function": {
        "name": "delete_records",
        "description": "删除记录：只会生成预告（影响行数与确认提示），用户确认后才执行。"
                       "ids 与 filter 二选一；无条件删除会被拒绝。",
        "parameters": {"type": "object", "properties": {
            "entity": {"type": "string"},
            "ids": {"type": "array", "items": {"type": "integer"}},
            "filter": {"type": "object"},
        }, "required": ["entity"]}}},
]


class ToolBox:
    """工具执行器。每次对话构建一个（绑定用户令牌与会话）。"""

    def __init__(self, client: SpringClient, cat: ModelCatalog, token: str):
        self._client = client
        self._catalog = cat
        self._token = token

    def _entity(self, name: str) -> EntityInfo:
        info = self._catalog.by_name(name or "")
        if info is None:
            known = "、".join(f"{e.display_name}({e.entity})" for e in self._catalog.all())
            raise ToolInputError(f"实体 {name!r} 不存在。可用实体：{known}")
        return info

    def _validate_filter(self, entity: EntityInfo, filter_: dict | None) -> dict | None:
        if not filter_:
            return None
        for condition in filter_.get("conditions") or []:
            operator = str(condition.get("operator", "")).upper()
            if operator not in OPERATORS:
                raise ToolInputError(f"不支持的操作符 {operator!r}，可用：{sorted(OPERATORS)}")
            field = entity.field(str(condition.get("field", "")))
            if field is None:
                known = "、".join(f.column for f in entity.fields)
                raise ToolInputError(f"字段 {condition.get('field')!r} 不在 {entity.entity} 里。可用：{known}")
            condition["field"] = field.column  # 语义名/属性名统一归一成列名
        return filter_

    async def call(self, name: str, args: dict, session: Any = None) -> dict[str, Any]:
        """执行一个工具调用。ToolInputError 与 SpringError 由图节点统一翻译。"""
        handlers = {
            "query_records": self._query,
            "aggregate_records": self._aggregate,
            "insert_records": self._insert,
            "update_records": self._update,
            "delete_records": self._delete,
        }
        if name not in handlers:
            raise ToolInputError(f"未知工具 {name!r}")
        return await handlers[name](args, session)

    async def _query(self, args: dict, session: Any) -> dict:
        entity = self._entity(args.get("entity", ""))
        request = {
            "filter": self._validate_filter(entity, args.get("filter")),
            "orderBy": [],
            "page": max(int(args.get("page") or 1), 1),
            "size": min(max(int(args.get("size") or 20), 1), 200),
        }
        data = await self._client.query(entity.entity, request, self._token)
        rows = data.get("rows", [])
        if session is not None:
            # 槽位更新（设计 3.1）：任何数据工具调用后记 lastEntity，query 后记条件与主键
            session.set_slot("lastEntity", {"entity": entity.entity, "display": entity.display_name})
            session.set_slot("lastFilter", request["filter"])
            session.set_slot("lastResultIds",
                             [row["id"] for row in rows if row.get("id") is not None][:20])
        return {"ok": True, "entity": entity.entity, "display": entity.display_name,
                "total": data.get("total", 0), "page": data.get("page", 1),
                **tool_result_for_llm(rows, data.get("total", 0)),
                "__rows__": rows}  # 给用户表格卡片用的原始行，不进 LLM 上下文

    async def _aggregate(self, args: dict, session: Any) -> dict:
        entity = self._entity(args.get("entity", ""))
        request = {
            "groupBy": [entity.field(str(g)).column if entity.field(str(g)) else str(g)
                        for g in (args.get("groupBy") or [])],
            "metrics": args.get("metrics") or [{"func": "COUNT", "alias": "cnt"}],
            "filter": self._validate_filter(entity, args.get("filter")),
        }
        data = await self._client.aggregate(entity.entity, request, self._token)
        if session is not None:
            session.set_slot("lastEntity", {"entity": entity.entity, "display": entity.display_name})
        return {"ok": True, "entity": entity.entity, "rows": data}

    async def _insert(self, args: dict, session: Any) -> dict:
        entity = self._entity(args.get("entity", ""))
        row = args.get("row") or {}
        unknown = [k for k in row if entity.field(str(k)) is None]
        if unknown:
            raise ToolInputError(f"字段 {unknown} 不在 {entity.entity} 里")
        normalized = {entity.field(str(k)).column: v for k, v in row.items()}
        data = await self._client.insert(entity.entity, normalized, self._token)
        return {"ok": True, "id": data, "display": entity.display_name}

    async def _update(self, args: dict, session: Any) -> dict:
        entity = self._entity(args.get("entity", ""))
        values = args.get("values") or {}
        unknown = [k for k in values if entity.field(str(k)) is None]
        if unknown:
            raise ToolInputError(f"字段 {unknown} 不在 {entity.entity} 里")
        normalized = {entity.field(str(k)).column: v for k, v in values.items()}
        record_id = args.get("id")
        if record_id is None:
            raise ToolInputError("update_records 需要 id")
        data = await self._client.update(entity.entity, {**normalized, "id": record_id}, self._token)
        return {"ok": True, "updated": data}

    async def _delete(self, args: dict, session: Any) -> dict:
        """删除只做预告。执行权在编排层的确认路径，模型拿不到令牌。"""
        entity = self._entity(args.get("entity", ""))
        ids = [int(i) for i in (args.get("ids") or []) if i is not None]
        if not ids and args.get("filter"):
            data = await self._client.query(
                entity.entity,
                {"filter": self._validate_filter(entity, args.get("filter")),
                 "orderBy": [], "page": 1, "size": MAX_DELETE_SCAN},
                self._token)
            ids = sorted({row["id"] for row in data.get("rows", []) if row.get("id") is not None})
            if data.get("total", 0) > MAX_DELETE_SCAN:
                return {"ok": False, "status": "too_many",
                        "total": data["total"],
                        "message": f"符合条件的{entity.display_name}有 {data['total']} 条，"
                                   f"超出单次删除上限（{MAX_DELETE_SCAN}），请缩小范围"}
        if not ids:
            return {"ok": False, "status": "no_target",
                    "message": f"删除{entity.display_name}需要明确范围（ids 或 filter），不支持无条件全删"}
        ticket = await self._client.preview_delete(entity.entity, ids, self._token)
        if session is not None:
            from datetime import datetime, timedelta
            session.set_slot("pending_confirm", {
                "token": ticket["token"], "entity": entity.entity, "ids": ids,
                "summary": ticket["summary"],
                "expiresAt": (datetime.now() + timedelta(minutes=4, seconds=50)).isoformat(timespec="seconds"),
            })
        return {"ok": True, "status": "need_confirmation",
                "summary": ticket["summary"], "ids": ids}


class ToolInputError(Exception):
    """模型给的工具参数不合法（实体/字段/操作符白名单拦截）。"""
