"""代码生成器：业务模型 JSON → 版本目录里的完整可编译 Spring 工程。

设计依据《代码生成器设计 v0.7》：
- 产物分两层：platform（整目录复制，不渲染）与 generated（模板渲染，每实体六文件）；
- 模型只填参数，不写代码；所有 import、方法签名由模板固定（G1/G3）；
- 字段类型只接受八种枚举映射（G2），跨实体不生成对象嵌套（G4）。

用法：
    PYTHONPATH=src .venv/bin/python -m syscraft.codegen.generator \
        --model models/trade_order.v1.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

# ---------------------------------------------------------------- 类型映射

# 模型的八种类型枚举 → Java 类型（G2：不接受自由字符串）
JAVA_TYPES = {
    "VARCHAR": "String",
    "TEXT": "String",
    "INT": "Integer",
    "BIGINT": "Long",
    "DECIMAL": "BigDecimal",
    "DATE": "LocalDate",
    "DATETIME": "LocalDateTime",
    "BOOLEAN": "Boolean",
}

# 需要额外 import 的 Java 类型
JAVA_IMPORTS = {
    "BigDecimal": "java.math.BigDecimal",
    "LocalDate": "java.time.LocalDate",
    "LocalDateTime": "java.time.LocalDateTime",
}

# 比较运算符 → Java 表达式运算符
COMPARE_OPS = {"GT": "<=", "GE": "<", "LT": ">=", "LE": ">", "EQ": "!=", "NE": "=="}

# 四则运算 → BigDecimal 方法
ARITHMETIC = {"MULTIPLY": "multiply", "ADD": "add", "SUBTRACT": "subtract"}

NUMERIC_JAVA = {"Integer", "Long", "BigDecimal"}


def java_type(field: dict) -> str:
    model_type = field["type"]
    if model_type not in JAVA_TYPES:
        raise SystemExit(f"不支持的字段类型 {model_type}（字段 {field['column']}），只接受 {list(JAVA_TYPES)}")
    return JAVA_TYPES[model_type]


def ddl_type(field: dict) -> str:
    model_type = field["type"]
    if model_type == "VARCHAR":
        return f"VARCHAR({field.get('length', 255)})"
    if model_type == "DECIMAL":
        return f"DECIMAL({field.get('precision', 12)},{field.get('scale', 2)})"
    if model_type == "BOOLEAN":
        return "TINYINT"
    if model_type == "TEXT":
        return "TEXT"
    return model_type


def ddl_line(field: dict) -> str:
    """拼一行列定义。DDL 是给 DBA 看的产物，不是运行时 SQL，允许字符串拼接。"""
    column, model_type = field["column"], field["type"]
    if field.get("primary_key"):
        return f"{column} BIGINT NOT NULL AUTO_INCREMENT COMMENT '{field['semantic']}'"

    parts = [column, ddl_type(field)]
    parts.append("NULL" if field.get("nullable") else "NOT NULL")
    if column == "created_at" and model_type == "DATETIME":
        parts.append("DEFAULT CURRENT_TIMESTAMP")
    return " ".join(parts) + f" COMMENT '{field['semantic']}'"


def getter_name(prop: str) -> str:
    return prop[0].upper() + prop[1:]


def prepare_field(field: dict) -> dict:
    enriched = dict(field)
    enriched["java_type"] = java_type(field)
    enriched["getter"] = getter_name(field["property"])
    enriched["ddl_line"] = ddl_line(field)
    enriched["nullable_int"] = 1 if field.get("nullable") else 0
    enriched["unique_int"] = 1 if field.get("unique") else 0
    enriched["sensitive_int"] = 1 if field.get("sensitive") else 0
    return enriched


def build_checks(entity: dict) -> dict:
    """校验段与规则段的 Java 片段由固定句式拼出，模型只提供字段名与阈值。

    规则里的字段名是**列名**（snake_case），实体字段同时带列名与属性名
    （camelCase）——手写模型两者常相同掩盖了这里的查找 bug，LLM 抽取的模型
    第一次分叉就炸（KeyError: borrow_quantity）。两种名字都认。"""
    fields = entity["_fields"]
    by_prop = {f["property"]: f for f in fields}
    by_column = {f["column"]: f for f in fields}

    def _resolve(name: str) -> dict | None:
        return by_prop.get(name) or by_column.get(name)

    constraint_rules = entity.get("rules", {}).get("constraints", [])
    constrained = {
        _resolve(c["field"]).get("property")
        for c in constraint_rules if _resolve(c["field"]) is not None
    }

    not_null_checks = []
    for f in fields:
        if f.get("primary_key") or f.get("nullable") or f["property"] in constrained:
            continue
        if f["java_type"] == "String":
            condition = f"e.get{f['getter']}() == null || e.get{f['getter']}().isBlank()"
        else:
            condition = f"e.get{f['getter']}() == null"
        not_null_checks.append({"condition": condition, "message": f"{f['semantic']}不能为空"})

    constraints = []
    for rule in constraint_rules:
        f = _resolve(rule["field"])
        if f is None:
            raise SystemExit(f"约束引用了不存在的字段 {rule['field']!r}")
        if f["java_type"] not in NUMERIC_JAVA:
            raise SystemExit(f"约束只支持数值字段（{rule['field']} 是 {f['java_type']}）")
        op = COMPARE_OPS.get(rule["op"])
        if op is None:
            raise SystemExit(f"不支持的约束运算符 {rule['op']}")
        value = rule["value"]
        if f["java_type"] == "BigDecimal":
            condition = f"e.get{f['getter']}() == null || e.get{f['getter']}().compareTo(BigDecimal.valueOf({value})) {op} 0"
        else:
            # 反向比较：GT 写成 <= 才好读——「不满足就抛」
            condition = f"e.get{f['getter']}() == null || e.get{f['getter']}() {op} {value}"
        constraints.append({"condition": condition, "message": rule["message"]})

    computed_rules = []
    for rule in entity.get("rules", {}).get("computed", []):
        target = _resolve(rule["target"])
        left = _resolve(rule["left"])
        right = _resolve(rule["right"])
        if target is None or left is None or right is None:
            raise SystemExit(f"计算规则 {rule} 引用了不存在的字段")
        method = ARITHMETIC.get(rule["op"])
        if method is None:
            raise SystemExit(f"不支持的计算运算符 {rule['op']}")
        left_expr = f"e.get{left['getter']}()" if left["java_type"] == "BigDecimal" else f"BigDecimal.valueOf(e.get{left['getter']}())"
        right_expr = f"e.get{right['getter']}()" if right["java_type"] == "BigDecimal" else f"BigDecimal.valueOf(e.get{right['getter']}())"
        computed_rules.append({
            "target_getter": target["getter"],
            "left_getter": left["getter"],
            "right_getter": right["getter"],
            "method": "." + method,
            "expression": f"{rule['target']} = {rule['left']} × {rule['right']}",
            "left_expr": left_expr,
            "right_expr": right_expr,
        })

    state_rule = None
    for rule in entity.get("rules", {}).get("states", []):
        f = _resolve(rule["field"])
        if f is None:
            raise SystemExit(f"状态机规则引用了不存在的字段 {rule['field']!r}")
        state_rule = {
            "field": rule["field"],
            "getter": f["getter"],
            "constant": rule["field"].upper() + "_FLOW",
            "flow": rule["flow"],
        }

    return {
        "not_null_checks": not_null_checks,
        "constraints": constraints,
        "computed_rules": computed_rules,
        "state_rule": state_rule,
    }


def collect_imports(fields: list[dict]) -> list[str]:
    imports = {JAVA_IMPORTS[f["java_type"]] for f in fields if f["java_type"] in JAVA_IMPORTS}
    return sorted(imports)


def render_entity(env: Environment, entity: dict, out_dir: Path, template_dir: str,
                  on_file=None) -> list[str]:
    """渲染一个实体的六个文件，返回写出的相对路径（给验收清单的 suspects 用）。
    on_file(relative)：每写一个文件回调一次（对话式生成用它推 SSE file 事件）。"""
    name = entity["class_name"]
    fields = entity["_fields"]
    visible_fields = [f for f in fields if f.get("visibility") != "HIDDEN"]

    checks = build_checks(entity)
    # 行级归属：模板据此渲染查询过滤/insert 写入/update·delete 校验
    owner_column = entity.get("owner_field")
    owner_field = next((f for f in fields if f["column"] == owner_column), None)
    context = {
        "entity": entity["entity"],
        "class_name": name,
        "display_name": entity["display_name"],
        "fields": fields,
        "visible_fields": visible_fields,
        "imports": collect_imports(fields),
        "unique_keys": [f for f in fields if f.get("unique") and not f.get("primary_key")],
        "owner_property": owner_field["property"] if owner_field else None,
        "owner_getter": owner_field["getter"] if owner_field else None,
        "owner_column": owner_column,
        **checks,
    }

    outputs = {
        "java/entity.java.j2": f"entity/{name}.java",
        "java/mapper.java.j2": f"mapper/{name}Mapper.java",
        "java/service.java.j2": f"service/{name}Service.java",
        "java/controller.java.j2": f"controller/{name}Controller.java",
        "java/query.java.j2": f"dto/{name}Query.java",
        "java/vo.java.j2": f"dto/{name}VO.java",
    }

    written = []
    for template_name, relative in outputs.items():
        target = out_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(env.get_template(template_name).render(**context), encoding="utf-8")
        written.append(relative)
        if on_file:
            on_file(relative)
    return written


def _java_string(value) -> str:
    """把任意文本转成可安全放进 Java 双引号字面量的形式。

    校验层（schema_map）已经拒绝引号/反斜杠/控制字符，这里是第二道：
    模板里所有来自模型或用户文本的字面量都过这个 filter，不让「上游某天
    少校验一处」直接变成编译失败。"""
    return (str(value).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))


def make_env(templates_dir: Path) -> Environment:
    # StrictUndefined：模板里写错变量名直接报错，而不是渲染出空字符串再让 javac 猜
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["java_str"] = _java_string
    return env


def _ensure_owner_field(entity: dict) -> None:
    """行级隔离（init.sql：owner_field 为空表示不做行级隔离）：
    模型声明了 owner_field 就自动补上归属列——靠 LLM 或手写模型自觉加列
    迟早漏，结构性的东西由生成器兜住（与 id/created_at 同一纪律）。"""
    owner = entity.get("owner_field")
    if not owner:
        return
    if any(f["column"] == owner for f in entity["fields"]):
        return
    prop = "".join(part.capitalize() for part in owner.split("_"))
    prop = prop[0].lower() + prop[1:]
    entity["fields"].append({
        "column": owner, "property": prop, "type": "BIGINT",
        "primary_key": False, "nullable": False, "unique": False,
        "semantic": "归属用户", "visibility": "PLAIN", "sensitive": False,
    })


def generate(model_path: Path, project_root: Path,
             on_file=None) -> dict:
    """on_file(relative_path)：每写一个产物文件回调一次（相对版本目录或项目根）。"""
    model_path = model_path.resolve()
    # 相对路径先落成绝对路径：下面要用 project_root.parent 去找同级骨架目录
    project_root = project_root.resolve()
    model = json.loads(model_path.read_text(encoding="utf-8"))
    version = model["model_version"]
    workspace = project_root / "workspace"
    version_dir = workspace / "versions" / version

    if version_dir.exists():
        # 已存在则拒绝，避免覆盖（《代码生成器设计》4 节）
        raise SystemExit(f"版本目录已存在：{version_dir}；换一个 model_version 或先手动删除")

    templates_dir = project_root / "templates"
    env = make_env(templates_dir)

    # ① 复制 platform 骨架与构建文件：整目录复制，不渲染
    skeleton = project_root.parent / "spring-skeleton"
    shutil.copytree(
        skeleton,
        version_dir,
        ignore=shutil.ignore_patterns("target", "README.md"),
    )

    # ② 逐实体渲染六个文件
    generated_root = version_dir / "src/main/java/com/generated/generated"
    # file 事件的路径语义统一为「相对版本目录」（产物文件）或「相对项目根」（脚本）——
    # 前端展示与排查都按版本目录找文件
    gen_prefix = "src/main/java/com/generated/generated"

    def file_cb(relative: str) -> None:
        if on_file:
            on_file(f"{gen_prefix}/{relative}")

    written = {}
    for entity in model["entities"]:
        _ensure_owner_field(entity)
        entity["_fields"] = [prepare_field(f) for f in entity["fields"]]
        written[entity["class_name"]] = render_entity(
            env, entity, generated_root, str(templates_dir), on_file=file_cb)

    # ②b 行级归属元数据：SnapshotService 导出快照时合并 owner_field 到 adm_entity_snapshot。
    #    放生成物而不放模型文件——产物必须能脱离平台独立运行（契约 5 节第 4 条）
    metadata_path = generated_root / "GeneratedMetadata.java"
    metadata_path.write_text(env.get_template("java/generated_metadata.java.j2").render(
        owner_fields={e["entity"]: e["owner_field"] for e in model["entities"] if e.get("owner_field")},
    ), encoding="utf-8")
    written["GeneratedMetadata"] = [str(metadata_path.relative_to(version_dir))]
    if on_file:
        on_file(str(metadata_path.relative_to(version_dir)))

    # ③ DDL 与默认授权（结构快照由产物启动时自查导出）
    ddl_dir = workspace / "scripts" / "ddl"
    ddl_dir.mkdir(parents=True, exist_ok=True)
    grants_dir = workspace / "scripts" / "grants"
    grants_dir.mkdir(parents=True, exist_ok=True)

    ddl_files, grant_files = [], []
    for index, entity in enumerate(model["entities"], start=1):
        ddl_path = ddl_dir / f"{version}_00{index}_{entity['entity']}.sql"
        unique_clause = "".join(
            f",\n    UNIQUE KEY uk_{entity['entity']}_{f['column']} ({f['column']})"
            for f in entity["_fields"] if f.get("unique") and not f.get("primary_key")
        )
        ddl_path.write_text(env.get_template("sql/table.sql.j2").render(
            entity=entity["entity"], fields=entity["_fields"], unique_clause=unique_clause,
            display_name=entity["display_name"], model_version=version,
        ), encoding="utf-8")
        ddl_files.append(str(ddl_path.relative_to(project_root)))
        if on_file:
            on_file(str(ddl_path.relative_to(project_root)))

        grants = [g for g in model.get("grants", []) if g["entity"] == entity["entity"]]
        # 结构快照不再由生成器写：产物启动时从 information_schema 自查导出，
        # 生成器只负责「新表默认给谁什么档位」这类策略。
        grants_path = grants_dir / f"{version}_00{index}_{entity['entity']}.sql"
        grants_path.write_text(env.get_template("sql/grants.sql.j2").render(
            entity=entity["entity"], grants=grants, model_version=version,
        ), encoding="utf-8")
        grant_files.append(str(grants_path.relative_to(project_root)))
        if on_file:
            on_file(str(grants_path.relative_to(project_root)))

    # ④ 验收清单：与代码同源，放在 templates/acceptance 下（不放版本目录，避免随回滚丢失）
    acceptance = build_acceptance(model, written)
    acceptance_dir = templates_dir / "acceptance"
    acceptance_dir.mkdir(parents=True, exist_ok=True)
    acceptance_path = acceptance_dir / f"{version}.json"
    acceptance_path.write_text(json.dumps(acceptance, ensure_ascii=False, indent=2), encoding="utf-8")
    if on_file:
        on_file(str(acceptance_path.relative_to(project_root)))

    return {
        "version": version,
        "version_dir": str(version_dir),
        "files": written,
        "ddl": ddl_files,
        "grants": grant_files,
        "acceptance": str(acceptance_path.relative_to(project_root)),
    }


def build_acceptance(model: dict, written: dict[str, list[str]]) -> dict:
    """验收清单：生成器负责「填什么」，运行时负责「怎么跑」。"""
    cases = [
        {"id": "health", "category": "service", "level": "block", "title": "健康检查为 UP",
         "check": {"type": "health", "path": "/actuator/health"}},
    ]
    # 超管身份用系统超管（凭证来自环境）：API 创建超管账号被委托约束禁止——这是防线不是障碍。
    # USER 才是临时验收号：走管理接口创建、验收完停用（运行时 3.3）。
    accounts = [
        {"role": "SUPER_ADMIN", "username": "__acc_admin__", "usernameRef": "env:ADMIN_USER",
         "passwordRef": "env:ADMIN_INIT_PWD"},
        {"role": "USER", "username": "__acc_user__", "passwordRef": "env:ACC_USER_PWD"},
    ]

    for entity in model["entities"]:
        base = f"/api/biz/{entity['entity']}"
        cases.append({
            "id": f"endpoints-{entity['entity']}", "category": "interface", "level": "block",
            "title": f"{entity['display_name']} 五个端点均存在",
            "suspects": [f"src/main/java/com/generated/generated/controller/{entity['class_name']}Controller.java"],
            "check": {"type": "endpoints", "base": base,
                      "ops": ["query", "aggregate", "insert", "update", "delete"]},
        })
        cases.append({
            "id": f"crud-{entity['entity']}", "category": "interface", "level": "block",
            "title": f"{entity['display_name']} 增删改查往返一致",
            "suspects": [f"src/main/java/com/generated/generated/service/{entity['class_name']}Service.java"],
            "check": {"type": "crud", "base": base, "entity": entity["entity"],
                      "account": "__acc_admin__",
                      "sample": {f["column"]: sample_value(f) for f in entity["fields"] if not f.get("primary_key")}},
        })

    first_base = f"/api/biz/{model['entities'][0]['entity']}"
    cases.append({
        "id": "confirm-reuse", "category": "confirm", "level": "block",
        "title": "同一确认令牌不能核销两次",
        "suspects": ["src/main/java/com/generated/platform/confirm/ConfirmService.java"],
        "check": {"type": "confirmReuse", "base": first_base, "entity": model["entities"][0]["entity"],
                  "account": "__acc_admin__",
                  "sample": {f["column"]: sample_value(f) for f in model["entities"][0]["fields"] if not f.get("primary_key")},
                  "unique_fields": [f["column"] for f in model["entities"][0]["fields"]
                                    if f.get("unique") and not f.get("primary_key")]},
    })

    cases.append({
        "id": "auth-401", "category": "permission", "level": "block", "title": "无令牌访问返回 401",
        "suspects": ["src/main/java/com/generated/platform/security/SaTokenConfig.java"],
        "check": {"type": "status", "path": f"{first_base}/query", "expect": 401, "auth": "none"},
    })
    cases.append({
        "id": "perm-403", "category": "permission", "level": "block", "title": "普通用户删除未经授权返回 403",
        "suspects": ["src/main/java/com/generated/platform/security/PermissionCodeResolver.java"],
        "check": {"type": "status", "path": f"{first_base}/delete", "expect": 403, "auth": "__acc_user__",
                  "auth_body": {"ids": [1]}},
    })
    cases.append({
        "id": "perm-200", "category": "permission", "level": "block", "title": "超管查询返回 200",
        "suspects": ["src/main/java/com/generated/platform/security/PermissionCodeResolver.java"],
        "check": {"type": "status", "path": f"{first_base}/query", "expect": 200, "auth": "__acc_admin__",
                  "auth_body": {}},
    })

    return {"version": 1, "modelVersion": model["model_version"], "accounts": accounts, "cases": cases}


def sample_value(field: dict):
    """验收数据的样本值：优先用模型里写死的 sample，否则按类型给默认值。

    按类型而非按字段名给值，是为了避免「订单号」这类语义猜测——
    猜错了会在验收里制造假失败。
    """
    if field.get("sample") is not None:
        return field["sample"]
    model_type = field["type"]
    if model_type in ("VARCHAR", "TEXT"):
        return "T" + field["property"][:8]
    if model_type in ("INT", "BIGINT"):
        return 2 if field["column"] != "id" else None
    if model_type == "DECIMAL":
        return 100 if field["column"] != "amount" else None
    if model_type == "BOOLEAN":
        return True
    if model_type == "DATETIME":
        return "2026-11-09T10:00:00"
    if model_type == "DATE":
        return "2026-11-09"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SysCraft 代码生成器")
    parser.add_argument("--model", required=True, help="模型 JSON 路径")
    parser.add_argument("--project-root", default=".", help="agent-platform 根目录")
    args = parser.parse_args(argv)

    result = generate(Path(args.model), Path(args.project_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
