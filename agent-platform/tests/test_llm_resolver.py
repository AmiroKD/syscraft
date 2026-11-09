"""LLM 意图解析：JSON 形态、字段校验、追问、降级。"""

from __future__ import annotations

import pytest

from syscraft.requirement.intent import IntentError, RuleBasedIntentResolver
from syscraft.requirement.llm_resolver import LLMIntentResolver


def resolver(llm_content: str) -> LLMIntentResolver:
    return LLMIntentResolver(fallback=RuleBasedIntentResolver(),
                             llm_call=lambda messages: llm_content)


def messages_of(r: LLMIntentResolver, text="查订单"):
    return r._messages(text)


def test_system_prompt_injects_entities_from_catalog():
    messages = messages_of(resolver("{}"))
    system = messages[0]["content"]
    assert "trade_order（订单）" in system
    assert "order_no(订单号)" in system
    assert "needClarification" in system


def test_plain_json_query():
    content = '{"action":"query","entity":"trade_order","conditions":[{"field":"status","operator":"EQ","value":"PAID"}],"size":5}'
    intent = resolver(content).resolve("看看已支付的订单")
    assert intent.op == "QUERY"
    assert intent.source == "llm"
    assert [(c.column, c.operator, c.value) for c in intent.conditions] == [("status", "EQ", "PAID")]
    assert intent.size == 5


def test_json_in_code_fence_is_parsed():
    content = '```json\n{"action":"query","entity":"trade_order","conditions":[]}\n```'
    intent = resolver(content).resolve("查订单")
    assert intent.op == "QUERY" and intent.conditions == ()


def test_semantic_field_name_is_mapped_to_column():
    content = '{"action":"delete","entity":"trade_order","conditions":[{"field":"订单号","operator":"EQ","value":"SO-1"}]}'
    intent = resolver(content).resolve("删掉SO-1那笔订单")
    assert intent.op == "DELETE"
    assert intent.conditions[0].column == "order_no"


def test_field_hallucination_is_dropped_not_executed():
    # LLM 编了个不存在的字段：该条件整条丢弃，剩余条件仍忠实于用户；全被丢弃等于无条件查询？
    # 不行——无条件查询会全表返回，但这是「查」，影响面可控；「删」在编排层还有一道条件必填
    content = '{"action":"query","entity":"trade_order","conditions":[{"field":"user_id","operator":"EQ","value":1}]}'
    intent = resolver(content).resolve("查张三的订单")
    assert intent.conditions == ()


def test_need_clarification_raises_question():
    content = '{"needClarification":true,"question":"要查哪张表？目前只有订单。"}'
    with pytest.raises(IntentError) as error:
        resolver(content).resolve("帮我处理一下")
    assert "要查哪张表" in str(error.value)


def test_bad_json_falls_back_to_rules():
    # 模型输出损坏：降级规则版。规则版解析「查订单」成功，source 仍是 rules
    intent = resolver("抱歉我不明白你的意思").resolve("查订单")
    assert intent.op == "QUERY"
    assert intent.source == "rules"


def test_unknown_entity_asks_back_via_fallback():
    # LLM 幻觉出不存在的实体 → 整条作废 → 规则版接手 → 它也不认识 → 问用户
    content = '{"action":"query","entity":"employee","conditions":[]}'
    with pytest.raises(IntentError) as error:
        resolver(content).resolve("查员工")
    assert "没认出" in str(error.value)


def test_delete_without_conditions_asks_user():
    content = '{"action":"delete","entity":"trade_order","conditions":[]}'
    with pytest.raises(IntentError) as error:
        resolver(content).resolve("把订单删了")
    assert "明确范围" in str(error.value)


def test_network_failure_falls_back(monkeypatch):
    r = resolver('{"action":"query","entity":"trade_order","conditions":[]}')

    def boom(messages):
        raise ConnectionError("网络不可达")

    r._llm_call = boom
    intent = r.resolve("查订单")
    assert intent.source == "rules"


def test_orderby_and_size_clamped():
    content = ('{"action":"query","entity":"trade_order","conditions":[],'
               '"orderBy":[{"field":"创建时间","direction":"DESC"}],"size":9999}')
    intent = resolver(content).resolve("看最近的订单")
    assert intent.order_by == (("created_at", "DESC"),)
    assert intent.size == 200
