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

import json
import re
from typing import Any

from syscraft.conversation.context import tool_result_for_llm
from syscraft.requirement.model_catalog import EntityInfo, ModelCatalog
from syscraft.tools.spring_client import SpringClient

# 查询 DSL 允许的操作符（与《通用数据接口设计》一致）
OPERATORS = {"EQ", "NE", "GT", "GE", "LT", "LE", "LIKE", "IN", "BETWEEN", "IS_NULL"}

# 模型常见的符号写法 → 协议操作符。宁可在这里翻译，也不要让一次符号差异
# 变成「工具调用失败」——模型下一轮很可能换个写法继续错。
_OPERATOR_ALIASES = {
    "=": "EQ", "==": "EQ", "EQ": "EQ",
    "!=": "NE", "<>": "NE", "NE": "NE",
    ">": "GT", ">=": "GE", "<": "LT", "<=": "LE",
    "~": "LIKE", "CONTAINS": "LIKE", "ILIKE": "LIKE",
}

# 聚合函数白名单（与产物侧 Metric 的封闭集合保持一致）
AGGREGATE_FUNCS = {"COUNT", "SUM", "AVG", "MIN", "MAX"}

_ALIAS_RE = re.compile(r"[^\w\u4e00-\u9fa5]")

# 预告阶段允许的最大匹配行数（与既有对话层一致：删 500 条的预告没人会细看）
MAX_DELETE_SCAN = 200

TOOLS_SPEC: list[dict] = [
    {"type": "function", "function": {
        "name": "query_records",
        "description": "按条件查询业务数据。filter 可为 null（查全部）。返回 total 与 rows。",
        "parameters": {"type": "object", "properties": {
            "entity": {"type": "string", "description": "实体表名，例如 activity / signup"},
            "filter": {
                "type": "object",
                "description": "过滤条件，可为 null。结构：{op: AND|OR, conditions: [{field, operator, value}]}",
                "properties": {
                    "op": {"type": "string", "enum": ["AND", "OR"]},
                    "conditions": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "description": "字段列名"},
                            "operator": {"type": "string",
                                         "enum": sorted(OPERATORS)},
                            "value": {"description": "比较值；IN/BETWEEN 用数组，IS_NULL 可省略"},
                        },
                        "required": ["field", "operator"],
                    }},
                },
            },
            "orderBy": {"type": "array", "description": "排序，例如 [{field: \"start_time\", direction: \"DESC\"}]",
                        "items": {"type": "object", "properties": {
                            "field": {"type": "string"},
                            "direction": {"type": "string", "enum": ["ASC", "DESC"]},
                        }, "required": ["field"]}},
            "page": {"type": "integer", "description": "页码，默认 1"},
            "size": {"type": "integer", "description": "每页条数，默认 20，最大 200"},
        }, "required": ["entity"]}}},
    {"type": "function", "function": {
        "name": "aggregate_records",
        "description": "分组统计。groupBy 为字段名数组；metrics 为 [{func: COUNT/SUM/AVG/MIN/MAX, field?, alias?}]。",
        "parameters": {"type": "object", "properties": {
            "entity": {"type": "string"},
            "groupBy": {"type": "array", "items": {"type": "string"}},
            "metrics": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "func": {"type": "string", "enum": sorted(AGGREGATE_FUNCS)},
                    "field": {"type": "string", "description": "SUM/AVG/MIN/MAX 必填"},
                    "alias": {"type": "string", "description": "结果列别名，默认 cnt"},
                },
                "required": ["func"],
            }},
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
        info = self._catalog.resolve(name)
        if info is None:
            known = "、".join(f"{e.display_name}({e.entity})" for e in self._catalog.all())
            raise ToolInputError(f"实体 {name!r} 不存在。可用实体：{known}")
        return info

    @staticmethod
    def _condition(raw: Any, entity: EntityInfo) -> dict:
        """把模型给的一条条件归一成协议形态（字段→列名、操作符→大写枚举）。"""
        if not isinstance(raw, dict):
            raise ToolInputError("filter.conditions 的每一项都必须是对象")
        raw_field = raw.get("field") or raw.get("column") or raw.get("name")
        if raw_field in (None, ""):
            raise ToolInputError("过滤条件缺少 field")
        field = entity.field(str(raw_field)) or entity.match_field(str(raw_field))
        if field is None:
            known = "、".join(f"{f.column}({f.semantic})" for f in entity.fields)
            raise ToolInputError(
                f"字段 {raw_field!r} 不在 {entity.entity} 里。可用字段：{known}")
        operator = str(raw.get("operator") or raw.get("op") or "EQ").strip().upper()
        operator = _OPERATOR_ALIASES.get(operator, operator)
        if operator not in OPERATORS:
            raise ToolInputError(
                f"不支持的操作符 {operator!r}，可用：{sorted(OPERATORS)}")
        value = raw.get("value")
        if operator == "LIKE":
            value = _like_value(value)
        elif operator == "IN":
            value = _as_list(value, "IN")
        elif operator == "BETWEEN":
            value = _as_range(value)
        elif operator == "IS_NULL":
            value = None
        return {"field": field.column, "operator": operator, "value": value}

    def _validate_filter(self, entity: EntityInfo, filter_: Any) -> dict | None:
        """过滤条件归一化。接受三种模型常见写法：

        1. 协议形态 ``{"op":"AND","conditions":[...]}``；
        2. 扁平单条件 ``{"field":"name","operator":"like","value":"读书"}``；
        3. 条件数组 ``[{...},{...}]`` 或 JSON 字符串。

        以前只认第 1 种，模型换一种写法就整轮失败——这是「工具经常调用失败」
        的主要来源之一。归一化放在最近的边界做，产物侧仍然只看到严格协议。
        """
        if filter_ in (None, "", {}, []):
            return None
        if isinstance(filter_, str):
            try:
                filter_ = json.loads(filter_)
            except ValueError as error:
                raise ToolInputError(f"filter 不是合法 JSON：{error}") from error
        if isinstance(filter_, list):
            filter_ = {"op": "AND", "conditions": filter_}
        if not isinstance(filter_, dict):
            raise ToolInputError("filter 必须是对象或条件数组")
        conditions = filter_.get("conditions")
        if conditions is None:
            # 扁平单条件 / 把 conditions 写成 conditions 里的单个对象
            if any(key in filter_ for key in ("field", "column", "name")):
                conditions = [filter_]
            elif not filter_:
                return None
            else:
                raise ToolInputError("filter 需要 conditions 数组")
        if not isinstance(conditions, list):
            raise ToolInputError("filter.conditions 必须是数组")
        normalized = [self._condition(item, entity) for item in conditions]
        if not normalized:
            return None
        op = str(filter_.get("op") or "AND").strip().upper()
        return {"op": "OR" if op == "OR" else "AND", "conditions": normalized}

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
        if not isinstance(args, dict):
            raise ToolInputError(f"{name} 的参数必须是 JSON 对象")
        return await handlers[name](args, session)

    async def _query(self, args: dict, session: Any) -> dict:
        entity = self._entity(args.get("entity", ""))
        request = {
            "filter": self._validate_filter(entity, args.get("filter")),
            "orderBy": _normalize_order(entity, args.get("orderBy")),
            "page": _int_arg(args.get("page"), 1, 1, 10_000),
            "size": _int_arg(args.get("size"), 20, 1, 200),
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
        # groupBy 严格白名单：未知字段在这里拦下（产物侧还有一道静态映射兜底，
        # 但错误要在最近的边界暴露，别让 LLM 的幻觉一路打到数据库）
        raw_groups = args.get("groupBy") or []
        if isinstance(raw_groups, str):
            raw_groups = [raw_groups]
        if not isinstance(raw_groups, list):
            raise ToolInputError("groupBy 必须是字段名数组")
        group_by = []
        for g in raw_groups:
            field = entity.field(str(g)) or entity.match_field(str(g))
            if field is None:
                known = "、".join(f.column for f in entity.fields)
                raise ToolInputError(f"分组字段 {g!r} 不在 {entity.entity} 里。可用：{known}")
            group_by.append(field.column)
        request = {
            "groupBy": group_by,
            "metrics": _normalize_metrics(entity, args.get("metrics")),
            "filter": self._validate_filter(entity, args.get("filter")),
        }
        data = await self._client.aggregate(entity.entity, request, self._token)
        if session is not None:
            session.set_slot("lastEntity", {"entity": entity.entity, "display": entity.display_name})
        return {"ok": True, "entity": entity.entity, "rows": data}

    async def _insert(self, args: dict, session: Any) -> dict:
        entity = self._entity(args.get("entity", ""))
        row = _as_object(args.get("row"), "row")
        if not row:
            raise ToolInputError("insert_records 需要 row，且至少要有一个字段")
        normalized = _normalize_row(entity, row)
        data = await self._client.insert(entity.entity, normalized, self._token)
        return {"ok": True, "id": data, "display": entity.display_name}

    async def _update(self, args: dict, session: Any) -> dict:
        entity = self._entity(args.get("entity", ""))
        values = _as_object(args.get("values"), "values")
        if not values:
            raise ToolInputError("update_records 需要 values（只放要修改的字段）")
        normalized = _normalize_row(entity, values)
        record_id = _int_arg(args.get("id"), None, 1, 2**63 - 1)
        if record_id is None:
            raise ToolInputError("update_records 需要正整数 id")
        data = await self._client.update(entity.entity, {**normalized, "id": record_id}, self._token)
        return {"ok": True, "updated": data}

    async def _delete(self, args: dict, session: Any) -> dict:
        """删除只做预告。执行权在编排层的确认路径，模型拿不到令牌。"""
        entity = self._entity(args.get("entity", ""))
        ids = []
        for item in _as_list(args.get("ids"), "ids") or []:
            parsed = _to_int(item)
            if parsed is not None:
                ids.append(parsed)
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


def _to_int(value: Any) -> int | None:
    """宽松整数解析：模型常把 id/page 写成 "15"、15.0 甚至 "15条"。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        pass
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return int(float(match.group()))
    except ValueError:
        return None


def _int_arg(value: Any, default: int | None, low: int, high: int) -> int | None:
    if value in (None, ""):
        return default
    number = _to_int(value)
    if number is None:
        raise ToolInputError(f"需要整数参数，收到 {value!r}")
    return min(max(number, low), high)


def _as_list(value: Any, label: str) -> list | None:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[,，、]", value) if part.strip()]
        return parts
    return [value]


def _as_range(value: Any) -> list:
    if isinstance(value, (list, tuple)):
        items = list(value)
    elif isinstance(value, str):
        items = [part.strip() for part in re.split(r"[~～至]", value) if part.strip()]
    else:
        items = [value]
    if len(items) != 2:
        raise ToolInputError("BETWEEN 的值必须是二元数组，例如 [\"2025-01-01\",\"2025-12-31\"]")
    return items


def _like_value(value: Any) -> str:
    """LIKE 值去掉 SQL 风格的通配符。

    产物侧会转义 ``%`` 与 ``_``（防全表扫描），模型如果按 SQL 习惯传
    ``%读书%``，转义后就变成「字面量 %读书%」——一条都查不到，模型还以为是
    数据问题继续重试。对话层的 LIKE 语义就是「包含」，两端通配符直接剥掉。
    """
    text = "" if value is None else str(value)
    return text.strip().strip("%").strip()


def _as_object(value: Any, label: str) -> dict:
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as error:
            raise ToolInputError(f"{label} 不是合法 JSON：{error}") from error
    if not isinstance(value, dict):
        raise ToolInputError(f"{label} 必须是对象")
    return value


def _normalize_order(entity: EntityInfo, raw: Any) -> list[dict]:
    """排序归一化：字段解析 + 方向大写。模型也会写 "start_time desc" 这种字符串。"""
    if not raw:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ToolInputError("orderBy 必须是数组")
    result = []
    for item in raw:
        if isinstance(item, str):
            parts = item.strip().split()
            raw_field = parts[0] if parts else ""
            raw_direction = parts[1] if len(parts) > 1 else "ASC"
        elif isinstance(item, dict):
            raw_field = item.get("field") or item.get("column")
            raw_direction = item.get("direction") or item.get("order") or "ASC"
        else:
            raise ToolInputError("orderBy 的每一项必须是对象或字符串")
        if not raw_field:
            raise ToolInputError("orderBy 缺少 field")
        field = entity.field(str(raw_field)) or entity.match_field(str(raw_field))
        if field is None:
            known = "、".join(f.column for f in entity.fields)
            raise ToolInputError(f"排序字段 {raw_field!r} 不在 {entity.entity} 里。可用：{known}")
        direction = str(raw_direction).strip().upper()
        if direction in ("-", "-1", "DESCENDING", "降序"):
            direction = "DESC"
        elif direction in ("+", "1", "ASCENDING", "升序"):
            direction = "ASC"
        result.append({"field": field.column,
                       "direction": "DESC" if direction == "DESC" else "ASC"})
    return result


def _normalize_row(entity: EntityInfo, row: dict) -> dict:
    """字段名归一成列名；未知字段一次性报全，模型才能一轮改对。"""
    normalized = {}
    unknown = []
    for key, value in row.items():
        field = entity.field(str(key))
        if field is None:
            unknown.append(str(key))
            continue
        normalized[field.column] = value
    if unknown:
        known = "、".join(f"{f.column}({f.semantic})" for f in entity.fields)
        raise ToolInputError(f"字段 {unknown} 不在 {entity.entity} 里。可用字段：{known}")
    return normalized


def _normalize_metrics(entity: EntityInfo, raw: Any) -> list[dict]:
    """聚合指标归一化：函数白名单、字段解析、别名兜底。

    产物侧对 func/alias 有严格校验（未知函数 400、非法别名 400），模型却常传
    ``count`` 小写或中文别名「数量」。在平台边界翻译掉，别让格式差异变成失败。
    """
    if not raw:
        return [{"func": "COUNT", "alias": "cnt"}]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ToolInputError("metrics 必须是数组")
    metrics = []
    for item in raw:
        if not isinstance(item, dict):
            raise ToolInputError("metrics 的每一项都必须是对象")
        func = str(item.get("func") or item.get("function") or "COUNT").strip().upper()
        if func not in AGGREGATE_FUNCS:
            raise ToolInputError(f"不支持的聚合函数 {func!r}，可用：{sorted(AGGREGATE_FUNCS)}")
        alias = _ALIAS_RE.sub("_", str(item.get("alias") or "")).strip("_")[:32]
        if not alias:
            alias = "cnt" if func == "COUNT" else func.lower()
        metric = {"func": func, "alias": alias}
        if func != "COUNT":
            raw_field = item.get("field") or item.get("column")
            field = entity.field(str(raw_field or "")) or entity.match_field(str(raw_field or ""))
            if field is None:
                known = "、".join(f.column for f in entity.fields)
                raise ToolInputError(
                    f"统计字段 {raw_field!r} 不在 {entity.entity} 里。可用：{known}")
            metric["field"] = field.column
        metrics.append(metric)
    return metrics


class ToolInputError(Exception):
    """模型给的工具参数不合法（实体/字段/操作符白名单拦截）。"""
