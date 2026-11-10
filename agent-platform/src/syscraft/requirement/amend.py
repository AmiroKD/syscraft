"""增量修改：给已有实体加字段（「自适应演化」的最小闭环）。

只做**加列**——不删列、不改类型、不改名。这三件事要么不可逆、要么需要
数据迁移；而加列是 `ADD COLUMN ... NULL DEFAULT NULL`，对存量行零影响，
可以在线完成并回滚。

一条完整链路：
    「给活动加一个备注字段」→ 解析实体与字段 → 展示计划 → 确认
    → ALTER TABLE → 全量重渲染（新版本号）→ 编译 → 启动 → 验收 → 切版本

为什么仍然全量重渲染而不是只改一个文件：生成物是模板实例化的整体，
版本目录必须自洽（回滚才有意义）。增量体现在「数据库只加一列、用户只说一句话」，
不体现在「只改一个 Java 文件」。

字段名与类型必须确定性推断：模型只负责理解用户说了什么（这里用规则），
不负责产出可执行的东西。词表命中即用，不命中给 ext_N——宁可名字朴素，
也不要为了好看去猜一个可能拼错的英文。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path

from syscraft import config
from syscraft.codegen.generator import ddl_type
from syscraft.requirement.model_catalog import EntityInfo, ModelCatalog
from syscraft.requirement.schema_map import FORBIDDEN_IN_TEXT

# 常见业务字段的中英映射。命中即用，不做音译/翻译（不可靠且不可解释）
COLUMN_WORDS: dict[str, str] = {
    "备注": "remark", "说明": "description", "描述": "description",
    "电话": "phone", "手机": "mobile", "联系方式": "contact", "邮箱": "email",
    "地址": "address", "数量": "qty", "金额": "amount", "价格": "price",
    "费用": "fee", "时间": "time", "日期": "date", "状态": "status",
    "名称": "name", "标题": "title", "图片": "image", "附件": "attachment",
    "链接": "link", "负责人": "owner_name", "联系人": "contact",
    "部门": "department",
    "性别": "gender", "年龄": "age", "库存": "stock", "评分": "score",
    "分类": "category", "编号": "code", "来源": "source", "优先级": "priority",
}

# 带前缀的句式：「给活动加一个备注字段」
_STRICT = re.compile(
    r"(?:给|为|帮)\s*(?P<entity>[^，,。！!？?\s]{1,16}?)\s*(?:表)?\s*"
    r"(?:增加|添加|新增|加)\s*(?:一个|个|一)?\s*"
    r"(?P<field>[^，,。！!？?\s]{1,16}?)\s*(?:字段|列|属性)?\s*$"
)
# 不带前缀时必须显式说「字段/列」：「活动加个备注字段」
_LOOSE = re.compile(
    r"(?P<entity>[^，,。！!？?\s]{1,16}?)\s*(?:表)?\s*"
    r"(?:增加|添加|新增|加)\s*(?:一个|个|一)?\s*"
    r"(?P<field>[^，,。！!？?\s]{1,16}?)\s*(?:字段|列|属性)\s*$"
)


@dataclass
class AmendRequest:
    entity: EntityInfo
    field_text: str
    raw: str


@dataclass
class FieldSpec:
    column: str
    property: str
    semantic: str
    type: str
    length: int | None = None
    precision: int | None = None
    scale: int | None = None

    def as_model_field(self) -> dict:
        field = {
            "column": self.column, "property": self.property, "type": self.type,
            # 新列一律可空：存量行没有值，NOT NULL 在严格模式下会直接失败
            "primary_key": False, "nullable": True, "unique": False,
            "semantic": self.semantic, "visibility": "PLAIN", "sensitive": False,
        }
        if self.length:
            field["length"] = self.length
        if self.precision:
            field["precision"] = self.precision
            field["scale"] = self.scale or 2
        return field


def parse_amend(message: str, catalog: ModelCatalog) -> AmendRequest | None:
    """从一句话里认出「给 <实体> 加 <字段>」。认不出返回 None（回落业务模式）。

    实体必须能在在线目录里命中——这是防误伤的硬条件：「给张三加一个账号」
    里的「张三」不是实体，就不该被当成建表需求。
    """
    text = (message or "").strip()
    match = _STRICT.search(text) or _LOOSE.search(text)
    if not match:
        return None
    entity_text = match.group("entity")
    entity = (catalog.by_name(entity_text)
              or catalog.by_mention(entity_text)
              or catalog.by_mention(text))
    if entity is None:
        return None
    field_text = _clean_field(match.group("field"))
    if not field_text:
        return None
    return AmendRequest(entity=entity, field_text=field_text, raw=text)


def build_field(text: str, existing_columns: set[str]) -> FieldSpec:
    """把「备注」这类自然语言片段变成可落库的字段定义。"""
    semantic = _sanitize(text)
    model_type, length, precision, scale = _infer_type(semantic)
    column = _column_for(semantic, existing_columns)
    return FieldSpec(
        column=column,
        # property 必须是合法 Java 标识符，只能从英文列名派生——
        # 从中文语义派生会生成 class 变量名「备注」，编译直接失败
        property=_camel(column),
        semantic=semantic,
        type=model_type, length=length, precision=precision, scale=scale,
    )


def alter_sql(entity_name: str, model_field: dict) -> str:
    """加列语句。只加可空列，不动存量数据；COMMENT 里的单引号按 SQL 规则翻倍。"""
    comment = str(model_field.get("semantic") or model_field["column"]).replace("'", "''")
    return (f"ALTER TABLE `{entity_name}` ADD COLUMN `{model_field['column']}` "
            f"{ddl_type(model_field)} NULL DEFAULT NULL COMMENT '{comment}'")


def load_online_model(version: str) -> tuple[dict, Path] | tuple[None, None]:
    """按 model_version 在 models/ 里找在线版本的模型原件。

    不能假定文件名是 auto_gen.vN.json：手工模型（trade_order.v1.json）与
    抽取模型（auto_gen.vN.json）命名不同，按内容找才可靠。
    """
    if not version:
        return None, None
    for path in sorted(config.MODELS_DIR.glob("*.json")):
        try:
            model = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if model.get("model_version") == version:
            return model, path
    return None, None


def append_field(model: dict, entity_name: str, spec: FieldSpec) -> str | None:
    """把字段追加进模型。返回错误文案（None = 成功）。

    重复判定同时看列名与语义：用户说「加个备注」两次，第二次必须是明确拒绝，
    而不是静默生成 remark_2 让人以为系统坏了。
    """
    entity = next((e for e in model.get("entities", []) if e["entity"] == entity_name), None)
    if entity is None:
        return f"模型里没有实体 {entity_name}"
    for existing in entity.get("fields", []):
        if existing["column"] == spec.column:
            return f"「{entity_name}」已经有 {spec.column} 列了"
        if spec.semantic and existing.get("semantic") == spec.semantic:
            return f"「{entity_name}」已经有「{spec.semantic}」这个字段了"
    entity.setdefault("fields", []).append(spec.as_model_field())
    return None


# ------------------------------------------------------------------ 内部工具

def _clean_field(text: str) -> str:
    value = (text or "").strip()
    for prefix in ("一个", "个", "一个新的", "新的", "新"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.strip(" 的了")


def _sanitize(text: str) -> str:
    """去掉会破坏 DDL/Java 的字符，而不是拒绝：用户说「加个'备注'」是自然表达，
    不是攻击。sanitize 后仍为空则退化成通用名。"""
    cleaned = "".join(ch for ch in str(text) if ch not in FORBIDDEN_IN_TEXT)
    return cleaned.strip() or "扩展字段"


def _infer_type(semantic: str) -> tuple[str, int | None, int | None, int | None]:
    """按语义关键词推断类型。顺序有讲究：先判时间/金额，再判数量——
    「报名截止时间」不该因为含「报名」而落进字符串。"""
    if any(word in semantic for word in ("时间", "日期", "截止", "生日")):
        return "DATETIME", None, None, None
    if any(word in semantic for word in ("金额", "价格", "费用", "单价", "工资")):
        return "DECIMAL", None, 12, 2
    if any(word in semantic for word in ("数量", "人数", "人数上限", "名额", "次数",
                                         "库存", "年龄", "评分")):
        return "INT", None, None, None
    if any(word in semantic for word in ("是否", "启用", "完成", "有效")):
        return "BOOLEAN", None, None, None
    if "地址" in semantic:
        return "VARCHAR", 255, None, None
    if any(word in semantic for word in ("备注", "说明", "描述")):
        return "VARCHAR", 255, None, None
    return "VARCHAR", 128, None, None


def _column_for(semantic: str, existing: set[str]) -> str:
    base = next((column for word, column in COLUMN_WORDS.items() if word in semantic), "")
    return _first_free(base or "ext", existing)


def _first_free(base: str, existing: set[str]) -> str:
    """给 base 找一个没被占用的列名。

    后缀只能用**字母**，不能用数字：property 由列名驼峰化而来
    （remark_2 → remark2），而 MyBatis 把 property 反向映射成列走的是
    「驼峰转下划线」——remark2 转不出 remark_2，运行时就是
    Unknown column 'remark2' in 'field list'（实测踩过，整个实体查询 500）。
    字母后缀双向可逆：remark_b ↔ remarkB ↔ remark_b。
    """
    if base not in existing:
        return base
    for index in range(1, 26):
        candidate = f"{base}_{chr(ord('a') + index)}"
        if candidate not in existing:
            return candidate
    raise ValueError(f"列名 {base} 的字母后缀已用尽，请换一个语义")


def _camel(column: str) -> str:
    parts = column.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])
