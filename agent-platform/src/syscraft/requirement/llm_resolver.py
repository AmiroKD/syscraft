"""LLM 意图解析（《代码生成器设计》6.8 的 JSON 输出适配，用在对话解析上）。

分层：
- LLM 只负责「把自然语言变成结构化 JSON」，实体与字段清单由 {@link model_catalog} 注入——
  模型看不到任何表结构之外的自由度，字段名是它唯一能填的词。
- 校验在平台：字段、操作符、实体逐一比对白名单，越界值整条作废。
- 失败降级：网络错误、超时、JSON 损坏、字段幻觉，一律退回规则版解析器。
  代价是「模糊说法」在降级时解析不了；收益是模型抖一下不至于整个系统不可用（演示保护）。

「追问」走 needClarification：LLM 判断信息不足时返回问题文本，
编排层把它当作解析错误回给用户——追问本来就是解析失败的一种体面形式。
"""

from __future__ import annotations

import json
import re
from typing import Callable, Protocol

import httpx

from syscraft import config
from syscraft.requirement.intent import Condition, Intent, IntentError
from syscraft.requirement.model_catalog import ModelCatalog, catalog as default_catalog

ALLOWED_OPERATORS = {"EQ", "NE", "GT", "GE", "LT", "LE", "LIKE", "IN", "BETWEEN", "IS_NULL"}

SYSTEM_PROMPT_TEMPLATE = """你是业务系统的对话解析器。把用户的一句话解析成 JSON 指令。

可用实体与字段（字段只能用列名，不能自己发明）：
{entities}

输出一个 JSON 对象，不要输出任何其它内容。三种形态：

查询：
{{"action": "query", "entity": "实体名", "conditions": [{{"field": "列名", "operator": "EQ", "value": ...}}], "orderBy": [{{"field": "列名", "direction": "DESC"}}], "size": 20}}

删除（conditions 必填，没有明确范围就不许删）：
{{"action": "delete", "entity": "实体名", "conditions": [...]}}

信息不足需要追问：
{{"needClarification": true, "question": "向用户提出的问题"}}

规则：
- operator 只能是 EQ/NE/GT/GE/LT/LE/LIKE/IN/BETWEEN/IS_NULL
- 用户没说的条件不要编造；「最近的/最新的」用 orderBy created_at DESC
- 「前N条」→ size=N；没说就用 20
- 实体不明确、或删除没有明确范围 → needClarification
- value 保持用户给的原值（数字就是数字，字符串就是字符串）"""

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class _BadOutput(Exception):
    """LLM 输出不可用：触发降级，不暴露给用户。"""


class LLMIntentResolver:
    """
    :param llm_call: 可注入的补全函数（messages → content 文本）。测试用它替代真实 HTTP。
    """

    def __init__(self, catalog: ModelCatalog | None = None,
                 fallback: Protocol | None = None,
                 llm_call: Callable[[list[dict]], str] | None = None):
        self._catalog = catalog or default_catalog(online_only=True)
        self._fallback = fallback
        self._llm_call = llm_call or self._http_call

    def resolve(self, message: str) -> Intent:
        text = (message or "").strip()
        try:
            content = self._llm_call(self._messages(text))
            parsed = _extract_json(content)
        except Exception:
            # 网络、超时、JSON 损坏：降级规则版。降级解析不了时它会自己问用户
            return self._fallback.resolve(message)

        if parsed.get("needClarification"):
            raise IntentError(str(parsed.get("question") or "请把需求说得更具体一些。"))

        entity = self._catalog.by_name(str(parsed.get("entity", "")))
        if entity is None:
            return self._fallback.resolve(message)  # 实体幻觉：整条作废

        action = str(parsed.get("action", "")).lower()
        if action not in ("query", "delete"):
            return self._fallback.resolve(message)
        if action == "delete" and not parsed.get("conditions"):
            raise IntentError(f"删除需要明确范围，比如「删除{entity.display_name}，订单号是SO-1」。")

        conditions = self._conditions(parsed.get("conditions"), entity)
        order_by = self._order_by(parsed.get("orderBy"), entity)
        size = parsed.get("size") if isinstance(parsed.get("size"), int) else 20
        return Intent(op=action.upper(), entity=entity, conditions=tuple(conditions),
                      order_by=tuple(order_by), page=1, size=max(1, min(size, 200)), source="llm")

    # ------------------------------------------------------------------ 内部

    def _messages(self, text: str) -> list[dict]:
        lines = []
        for entity in self._catalog.all():
            fields = "，".join(f"{f.column}({f.semantic})" for f in entity.fields)
            lines.append(f"- 实体 {entity.entity}（{entity.display_name}）：{fields}")
        system = SYSTEM_PROMPT_TEMPLATE.format(entities="\n".join(lines) or "（暂无实体）")
        return [{"role": "system", "content": system},
                {"role": "user", "content": text}]

    def _http_call(self, messages: list[dict]) -> str:
        response = httpx.post(
            f"{config.LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json={"model": config.LLM_MODEL, "messages": messages,
                  "max_tokens": config.LLM_MAX_TOKENS, "temperature": 0},
            timeout=config.LLM_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            raise _BadOutput(f"HTTP {response.status_code}")
        choice = response.json()["choices"][0]
        return choice["message"]["content"] or ""

    def _conditions(self, raw, entity) -> list[Condition]:
        conditions: list[Condition] = []
        if not isinstance(raw, list):
            return conditions
        for item in raw:
            if not isinstance(item, dict):
                continue
            field = entity.match_field(str(item.get("field", "")))
            operator = str(item.get("operator", "EQ")).upper()
            if field is None or operator not in ALLOWED_OPERATORS:
                # 单条字段幻觉：丢弃该条件而不是整条作废，剩余条件仍忠实于用户
                continue
            if operator == "IS_NULL":
                conditions.append(Condition(column=field.column, operator="IS_NULL", value=None))
            else:
                conditions.append(Condition(column=field.column, operator=operator, value=item.get("value")))
        return conditions

    def _order_by(self, raw, entity) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        if not isinstance(raw, list):
            return result
        for item in raw:
            if not isinstance(item, dict):
                continue
            field = entity.match_field(str(item.get("field", "")))
            direction = str(item.get("direction", "ASC")).upper()
            if field is not None and direction in ("ASC", "DESC"):
                result.append((field.column, direction))
        return result


def _extract_json(content: str) -> dict:
    """剥代码围栏，或截取首个 {...}。解析失败抛 _BadOutput → 降级。"""
    text = (content or "").strip()
    match = _FENCE.search(text)
    if match:
        text = match.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise _BadOutput("没有 JSON")
    parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise _BadOutput("不是对象")
    return parsed
