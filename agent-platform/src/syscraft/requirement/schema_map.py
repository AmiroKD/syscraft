"""业务模型校验与形态映射（《需求到模式映射设计》2/5 节）。

LLM 产出的是「中间表示」（entities/relations/rules/roles），
生成器吃的是「业务模型 schema」。中间必须过两道：

1. 校验（5 节，全部确定性）：标识符、结构完整、关联一致、语义安全——
   不依赖模型的自觉，越界即拒；
2. 映射：中间表示 → 生成器 schema（注入 id/created_at、表达式解析成规则三元组、
   snake_case 转 camelCase）。映射是纯函数，错了可单测，不会静默。

设计上最重要的约束（2.1 节）：规则只允许 computed/constraint/state 三种受限形态，
模型只做参数填充——表达式的解析在平台做，模型永远不产出可执行代码。
"""

from __future__ import annotations

import re

# 八种类型枚举（G2），与生成器 JAVA_TYPES 对应
FIELD_TYPES = {"VARCHAR", "TEXT", "INT", "BIGINT", "DECIMAL", "DATE", "DATETIME", "BOOLEAN"}

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{1,30}$")

# MySQL 保留字里业务取名最容易撞的一批（全量清单见 MySQL 文档，这里挡常见误伤）
RESERVED = {"order", "group", "user", "key", "index", "desc", "asc", "limit", "select",
            "insert", "update", "delete", "where", "table", "create", "drop", "status_code"}

# 语义安全词表（5.4）：命中即标记敏感。只上调不下调，由调用方比对旧标记
SENSITIVE_WORDS = ("薪资", "工资", "身份证", "手机", "银行卡", "密码", "银行账号")

_COMPUTED = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*(\*|\+|-)\s*([a-z_][a-z0-9_]*)\s*$")
_CONSTRAINT = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*(>=|<=|>|<|==|!=)\s*(-?\d+(?:\.\d+)?)\s*$")
_ARITHMETIC = {"*": "MULTIPLY", "+": "ADD", "-": "SUBTRACT"}
_COMPARE = {">": "GT", ">=": "GE", "<": "LT", "<=": "LE", "==": "EQ", "!=": "NE"}


class ModelValidationError(Exception):
    """校验失败清单，一次性全部报出——喂回 LLM 自修正，或直接给用户。"""

    def __init__(self, problems: list[str]):
        super().__init__("；".join(problems[:8]))
        self.problems = problems


def validate(ir: dict, existing_entities: set[str] | None = None) -> list[str]:
    """确定性校验，返回问题清单（空 = 通过）。"""
    problems: list[str] = []
    entities = ir.get("entities") or []
    if not entities:
        return ["至少需要一个实体"]

    names: set[str] = set()
    for entity in entities:
        name = str(entity.get("name", ""))
        if not IDENTIFIER.match(name):
            problems.append(f"实体名 {name!r} 不合法（小写字母开头，仅小写字母/数字/下划线）")
        elif name in RESERVED:
            problems.append(f"实体名 {name!r} 是保留字")
        elif name.startswith("adm_"):
            problems.append(f"实体名 {name!r} 占用了平台前缀 adm_")
        elif existing_entities and name in existing_entities:
            problems.append(f"实体 {name} 与已有表重名（本次不支持增量，请换名）")
        names.add(name)

        columns = entity.get("columns") or []
        if not 1 <= len(columns) <= 40:
            problems.append(f"实体 {name} 字段数量 {len(columns)} 超出 1-40")
        seen: set[str] = set()
        for column in columns:
            field = str(column.get("name", ""))
            if not IDENTIFIER.match(field):
                problems.append(f"{name}.{field} 字段名不合法")
            elif field in seen:
                problems.append(f"{name}.{field} 字段重复")
            seen.add(field)
            if str(column.get("type", "")) not in FIELD_TYPES:
                problems.append(f"{name}.{field} 类型 {column.get('type')!r} 不在八种枚举内")

    # 关联一致性：引用目标必须存在，外键字段必须已在子表列里
    for relation in ir.get("relations") or []:
        from_field = str(relation.get("from", ""))
        to_ref = str(relation.get("to", ""))
        from_entity = from_field.split(".")[0] if "." in from_field else from_field
        to_entity = to_ref.split(".")[0] if "." in to_ref else to_ref
        if from_entity not in names:
            problems.append(f"关系 {from_field} → {to_ref}：来源实体 {from_entity} 不存在")
        if to_entity not in names:
            problems.append(f"关系 {from_field} → {to_ref}：目标实体 {to_entity} 不存在")
        if "." in from_field:
            column = from_field.split(".")[1]
            source = next((e for e in entities if e.get("name") == from_entity), None)
            if source and not any(c.get("name") == column for c in source.get("columns") or []):
                problems.append(f"关系 {from_field}：外键字段 {column} 不在 {from_entity} 的字段里")

    # 成环检测（自引用除外）：沿 MANY_TO_ONE 走一圈回到起点即环
    edges: dict[str, str] = {}
    for relation in ir.get("relations") or []:
        from_entity = str(relation.get("from", "")).split(".")[0]
        to_entity = str(relation.get("to", "")).split(".")[0]
        if from_entity != to_entity:
            edges[from_entity] = to_entity
    for start in list(edges):
        seen, node = set(), start
        while node in edges:
            if node in seen:
                problems.append(f"实体关系成环：{' → '.join(list(seen) + [node])}")
                break
            seen.add(node)
            node = edges[node]

    return problems


def sensitive_fields(entity: dict) -> set[str]:
    """语义安全词表命中（5.4）。只上报，不改动字段定义。"""
    hits = set()
    for column in entity.get("columns") or []:
        text = f"{column.get('name', '')}{column.get('semantic', '')}"
        if any(word in text for word in SENSITIVE_WORDS):
            hits.add(str(column.get("name")))
    return hits


def to_business_model(ir: dict, model_version: str) -> dict:
    """中间表示 → 生成器 schema。校验通过后才调用；表达式解析不了直接抛错。"""
    entities_out = []
    for entity in ir.get("entities") or []:
        name = entity["name"]
        class_name = "".join(part.capitalize() for part in name.split("_"))
        fields_out = [{
            "column": "id", "property": "id", "type": "BIGINT", "primary_key": True,
            "nullable": False, "unique": True, "semantic": "主键",
            "visibility": "PLAIN", "sensitive": False,
        }]
        for column in entity.get("columns") or []:
            field = _field_out(column)
            fields_out.append(field)
        # created_at 系统注入：审计与「最近的」排序都依赖它（模板 DDL 对它有默认值特判）
        fields_out.append({
            "column": "created_at", "property": "createdAt", "type": "DATETIME",
            "primary_key": False, "nullable": False, "unique": False, "semantic": "创建时间",
            "visibility": "PLAIN", "sensitive": False,
        })

        rules = _rules_out(entity.get("name"), entity.get("rules") or {}, fields_out)
        entities_out.append({
            "entity": name,
            "class_name": class_name,
            "display_name": entity.get("displayName") or name,
            "owner_field": entity.get("ownerField"),
            "fields": fields_out,
            "rules": rules,
        })

    # 建议角色不自动创建（2.3 条三：是否创建由用户确认）；默认授权保守收敛到内置 USER 只读，
    # 自定义角色与写入档位留给管理员显式上调——这条取舍写进假设
    grants = [{"role": "USER", "entity": e["entity"], "access": "READ"} for e in entities_out]
    assumptions = list(ir.get("assumptions") or [])
    assumptions.append("建议角色未自动创建（是否创建由用户确认）；内置 USER 角色默认只读，"
                       "写入权需管理员显式上调")
    return {"model_version": model_version, "entities": entities_out,
            "grants": grants, "assumptions": assumptions}


def _field_out(column: dict) -> dict:
    column_name = column["name"]
    prop = "".join(part.capitalize() for part in column_name.split("_"))
    prop = prop[0].lower() + prop[1:]
    field = {
        "column": column_name, "property": prop, "type": column["type"],
        "primary_key": False, "nullable": bool(column.get("nullable", True)),
        "unique": bool(column.get("unique", False)),
        "semantic": column.get("semantic") or column_name,
        "visibility": "PLAIN", "sensitive": bool(column.get("sensitive", False)),
    }
    if column.get("length"):
        field["length"] = column["length"]
    if column.get("precision"):
        field["precision"] = column["precision"]
        field["scale"] = column.get("scale", 2)
    return field


def _rules_out(entity_name: str, rules: dict, fields_out: list[dict]) -> dict:
    """三种受限形态 → 生成器规则结构。表达式在平台解析，模型只填参数（2.1 节）。"""
    known = {f["column"] for f in fields_out}
    computed, constraints, states = [], [], []
    for rule in rules.get("computed") or []:
        target = str(rule.get("target", "")).split(".")[-1]
        match = _COMPUTED.match(str(rule.get("expr", "")))
        if not match:
            raise ModelValidationError([f"{entity_name}: 计算规则 {rule.get('expr')!r} 不是「字段 运算符 字段」形态"])
        left, op, right = match.groups()
        if target not in known or left not in known or right not in known:
            raise ModelValidationError([f"{entity_name}: 计算规则 {rule.get('expr')!r} 引用了不存在的字段"])
        computed.append({"target": target, "op": _ARITHMETIC[op], "left": left, "right": right})

    for rule in rules.get("constraint") or []:
        match = _CONSTRAINT.match(str(rule.get("expr", "")))
        if not match:
            raise ModelValidationError([f"{entity_name}: 约束规则 {rule.get('expr')!r} 不是「字段 运算符 数值」形态"])
        field, op, value = match.groups()
        if field not in known:
            raise ModelValidationError([f"{entity_name}: 约束规则引用了不存在的字段 {field}"])
        constraints.append({"field": field, "op": _COMPARE[op], "value": float(value) if "." in value else int(value),
                            "message": f"{field} 必须满足 {op} {value}"})

    for rule in rules.get("state") or []:
        field = str(rule.get("field", "")).split(".")[-1]
        flow = [str(s) for s in rule.get("flow") or []]
        if field not in known or len(flow) < 2:
            raise ModelValidationError([f"{entity_name}: 状态机规则不合法（字段必须存在且至少两个状态）"])
        states.append({"field": field, "flow": flow})

    # 确定性兜底：字段语义写了「正数」的数值字段，若 LLM 漏了对应 constraint，自动补 GT 0。
    # 靠提示词指望模型每次都记得，不如在映射层用规则兜住——校验类的活儿本来就该确定性干（设计 5 节）
    for field in fields_out:
        if field["type"] in ("INT", "BIGINT", "DECIMAL") and "正数" in (field["semantic"] or ""):
            if not any(c["field"] == field["column"] for c in constraints):
                constraints.append({"field": field["column"], "op": "GT", "value": 0,
                                    "message": f"{field['semantic']}（来自字段语义推断）"})

    return {"computed": computed, "constraints": constraints, "states": states}
