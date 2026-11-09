"""把一句话变成一次数据操作。

当前是<b>确定性规则</b>实现：认实体、认「字段 是 值」、认条数。
真正的需求解析（LLM + 追问 + ModelDiff）在后面阶段接进来，届时实现同一个
`IntentResolver` 协议即可，编排层不用改。

规则版覆盖不到就问回去，绝不猜——猜错会静默查错数据，比报错难查得多。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from syscraft.requirement.model_catalog import EntityInfo, ModelCatalog, catalog

QUERY_WORDS = ("查", "看", "列", "有哪些", "多少", "统计", "汇总")
DELETE_WORDS = ("删除", "删掉", "去掉", "移除")
# 新增/修改仍不支持：它们的预告形态（字段值清单）和删除不同，等做那两类预告时再放开
WRITE_WORDS = ("新增", "录入", "添加", "修改", "改成", "更新")

FIELD_VALUE = re.compile(r"([\u4e00-\u9fa5A-Za-z_]{1,16})\s*(?:是|为|等于|=)\s*([^\s，,。；;]+)")
COUNT_WORDS = re.compile(r"(\d+)\s*(?:条|行|个|笔)")


class IntentError(Exception):
    """解析不出可执行意图。消息直接回给用户，所以要写人话。"""


@dataclass(frozen=True)
class Condition:
    column: str
    operator: str
    value: str


@dataclass(frozen=True)
class Intent:
    op: str
    entity: EntityInfo
    conditions: tuple[Condition, ...] = ()
    order_by: tuple[tuple[str, str], ...] = ()
    page: int = 1
    size: int = 20
    # 解析来源：rules=规则版 / llm=模型。排障时能区分「这句话是谁理解的」
    source: str = "rules"


class IntentResolver(Protocol):
    def resolve(self, message: str) -> Intent: ...


@dataclass
class RuleBasedIntentResolver:
    """规则版。只做查询；写入类请求明确拒绝，避免「以为已经支持了」。"""

    catalog: ModelCatalog = field(default_factory=lambda: catalog(online_only=True))

    def resolve(self, message: str) -> Intent:
        text = (message or "").strip()
        if not text:
            raise IntentError("请说一句话，比如「查订单」。")

        entity = self.catalog.by_mention(text)
        if entity is None:
            names = "、".join(f"「{e.display_name}」" for e in self.catalog.all()) or "（模型里还没有实体）"
            raise IntentError(f"没认出要操作哪张表。目前支持：{names}")

        op = "QUERY"
        if any(word in text for word in DELETE_WORDS):
            # 删除走「预告 → 用户确认 → 核销」流程，条件只允许精确匹配，
            # 模糊条件的影响面说不清，预告文案也就没法负责
            op = "DELETE"
        elif any(word in text for word in WRITE_WORDS):
            raise IntentError(f"这一版的新增 / 修改还没接上，目前支持查询与删除。")

        conditions = self._conditions(text, entity)
        if op == "DELETE" and not conditions:
            raise IntentError(
                f"删除需要明确范围，比如「删除{entity.display_name}，订单号是SO-1」；不支持无条件全删。"
            )

        size = self._size(text)
        order_by = (("created_at", "DESC"),) if entity.field("created_at") else ()

        return Intent(op=op, entity=entity, conditions=tuple(conditions),
                      order_by=order_by, page=1, size=size)

    def _conditions(self, text: str, entity: EntityInfo) -> list[Condition]:
        conditions: list[Condition] = []
        for raw_name, raw_value in FIELD_VALUE.findall(text):
            match = entity.match_field(raw_name)
            if match is None:
                continue
            conditions.append(Condition(column=match.column, operator="EQ", value=raw_value.strip("\"'“”")))
        return conditions

    def _size(self, text: str) -> int:
        found = COUNT_WORDS.search(text)
        if not found:
            return 20
        # 上限与产物侧一致：超过 200 会被截断，这里先夹住，免得回复里说「给你 500 条」
        return max(1, min(int(found.group(1)), 200))
