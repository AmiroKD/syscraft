"""意图解析的规则覆盖：认得出来、认得准、认不出来要问回去。"""

from __future__ import annotations

import pytest

from syscraft.requirement.intent import IntentError, RuleBasedIntentResolver


@pytest.fixture()
def resolver() -> RuleBasedIntentResolver:
    return RuleBasedIntentResolver()


def test_query_entity_only(resolver):
    intent = resolver.resolve("查订单")
    assert intent.op == "QUERY"
    assert intent.entity.entity == "trade_order"
    assert intent.conditions == ()
    assert intent.size == 20


def test_condition_and_limit(resolver):
    intent = resolver.resolve("看一下订单，状态是CREATED，前5条")
    assert [(c.column, c.operator, c.value) for c in intent.conditions] == [("status", "EQ", "CREATED")]
    assert intent.size == 5


def test_field_semantic_is_mapped_to_column(resolver):
    intent = resolver.resolve("查订单 订单号是SO-1")
    assert [(c.column, c.value) for c in intent.conditions] == [("order_no", "SO-1")]


def test_size_is_clamped(resolver):
    intent = resolver.resolve("查订单 前999条")
    assert intent.size == 200


def test_unknown_entity_asks_back(resolver):
    with pytest.raises(IntentError) as error:
        resolver.resolve("查员工")
    assert "没认出" in str(error.value)


def test_write_operations_are_rejected_explicitly(resolver):
    # 明确拒绝优于静默当成查询：用户以为删了、实际只是查了一遍，是最坏的结果
    with pytest.raises(IntentError) as error:
        resolver.resolve("删除订单")
    assert "只通了查询" in str(error.value)


def test_empty_message(resolver):
    with pytest.raises(IntentError):
        resolver.resolve("   ")
