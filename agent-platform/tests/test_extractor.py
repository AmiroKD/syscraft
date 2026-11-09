"""需求抽取：确定性校验、中间表示映射、追问纪律（LLM 注入，不碰真网络）。"""

from __future__ import annotations

import json

import pytest

from syscraft.requirement import schema_map
from syscraft.requirement.extractor import RequirementExtractor


IR_INVENTORY = {
    "entities": [
        {"name": "product", "displayName": "商品", "columns": [
            {"name": "name", "type": "VARCHAR", "length": 64, "semantic": "商品名称", "nullable": False},
            {"name": "price", "type": "DECIMAL", "precision": 12, "scale": 2, "semantic": "单价"},
            {"name": "stock_qty", "type": "INT", "semantic": "库存数量"},
            {"name": "alarm_threshold", "type": "INT", "semantic": "预警阈值"},
        ], "rules": {"constraint": [{"type": "constraint", "target": "product.price", "expr": "price > 0"}]}},
        {"name": "stock_in", "displayName": "入库单", "columns": [
            {"name": "in_no", "type": "VARCHAR", "length": 32, "semantic": "入库编号", "nullable": False, "unique": True},
            {"name": "product_id", "type": "BIGINT", "semantic": "商品"},
            {"name": "qty", "type": "INT", "semantic": "入库数量"},
            {"name": "operator", "type": "VARCHAR", "length": 32, "semantic": "经办人"},
        ]},
    ],
    "relations": [{"from": "stock_in.product_id", "to": "product.id", "type": "MANY_TO_ONE"}],
    "rules": {"computed": [{"type": "computed", "target": "product.price", "expr": "price * 1"}]},
    "roles": [],
    "assumptions": ["库存预警只存阈值，不做自动提醒"],
}


# ---------------------------------------------------------------- 校验器

def test_validate_identifier_rules():
    problems = schema_map.validate({"entities": [
        {"name": "Order", "columns": [{"name": "aa", "type": "INT"}]},
        {"name": "order", "columns": [{"name": "aa", "type": "INT"}]},
        {"name": "adm_x", "columns": [{"name": "aa", "type": "INT"}]},
    ]})
    assert len(problems) == 3  # 大写、保留字、平台前缀


def test_validate_types_and_duplicates():
    problems = schema_map.validate({"entities": [
        {"name": "demo", "columns": [
            {"name": "aa", "type": "LONG"},
            {"name": "aa", "type": "INT"},
            {"name": "bb", "type": "INT"}, {"name": "bb", "type": "INT"},
        ]},
    ]})
    assert any("类型" in p for p in problems)
    assert any("重复" in p for p in problems)


def test_validate_relation_targets():
    problems = schema_map.validate({"entities": [
        {"name": "stock_in", "columns": [{"name": "product_id", "type": "BIGINT"}]},
    ], "relations": [{"from": "stock_in.product_id", "to": "product.id", "type": "MANY_TO_ONE"}]})
    assert any("product 不存在" in p for p in problems)


def test_validate_cycle_detection():
    problems = schema_map.validate({"entities": [
        {"name": "a", "columns": [{"name": "b_id", "type": "BIGINT"}]},
        {"name": "b", "columns": [{"name": "c_id", "type": "BIGINT"}]},
        {"name": "c", "columns": [{"name": "a_id", "type": "BIGINT"}]},
    ], "relations": [
        {"from": "a.b_id", "to": "b.id", "type": "MANY_TO_ONE"},
        {"from": "b.c_id", "to": "c.id", "type": "MANY_TO_ONE"},
        {"from": "c.a_id", "to": "a.id", "type": "MANY_TO_ONE"},
    ]})
    assert any("成环" in p for p in problems)


def test_sensitive_words_flagged():
    hits = schema_map.sensitive_fields({"columns": [
        {"name": "phone", "semantic": "手机号"}, {"name": "salary", "semantic": "薪资"},
        {"name": "qty", "semantic": "数量"}]})
    assert hits == {"phone", "salary"}


# ---------------------------------------------------------------- 映射

def test_to_business_model_injects_id_and_created_at():
    model = schema_map.to_business_model(IR_INVENTORY, "v3")
    product = next(e for e in model["entities"] if e["entity"] == "product")
    assert product["fields"][0]["column"] == "id" and product["fields"][0]["primary_key"]
    assert product["fields"][-1]["column"] == "created_at"
    assert product["class_name"] == "Product"
    stock_in = next(e for e in model["entities"] if e["entity"] == "stock_in")
    fk = next(f for f in stock_in["fields"] if f["column"] == "product_id")
    assert fk["property"] == "productId"  # snake_case → camelCase
    assert model["grants"] == [{"role": "USER", "entity": "product", "access": "READ"},
                               {"role": "USER", "entity": "stock_in", "access": "READ"}]


def test_computed_rule_parsed_to_triple():
    ir = {"entities": [{"name": "demo", "columns": [
        {"name": "qty", "type": "INT"}, {"name": "price", "type": "DECIMAL"}, {"name": "amount", "type": "DECIMAL"}],
        "rules": {"computed": [{"type": "computed", "target": "demo.amount", "expr": "qty * price"}]}}]}
    model = schema_map.to_business_model(ir, "v1")
    rules = model["entities"][0]["rules"]["computed"]
    assert rules == [{"target": "amount", "op": "MULTIPLY", "left": "qty", "right": "price"}]


def test_unsupported_expression_is_rejected():
    ir = {"entities": [{"name": "demo", "columns": [
        {"name": "qty", "type": "INT"}, {"name": "amount", "type": "DECIMAL"}],
        "rules": {"computed": [{"type": "computed", "target": "demo.amount", "expr": "SUM(qty)"}]}}]}
    with pytest.raises(schema_map.ModelValidationError):
        schema_map.to_business_model(ir, "v1")


# ---------------------------------------------------------------- 抽取器编排

def extractor_with(content: str) -> RequirementExtractor:
    return RequirementExtractor(llm_call=lambda messages: content, existing_entities={"trade_order"})


def test_clarification_returns_at_most_three_questions():
    content = json.dumps({"needClarification": True, "questions": [
        {"question": "金额含税吗", "default": "未税"},
        {"question": "状态有哪几种", "default": "待审/已审/完成"},
        {"question": "谁能作废", "default": "仅管理员"},
        {"question": "第四个问题（应被截掉）", "default": "x"}]})
    result = extractor_with(content).extract("订单管理")
    assert result.needs_clarification and len(result.questions) == 3


def test_answers_suppress_second_clarification():
    # 用户放弃回答后模型仍追问 → 强制按默认值建模（4.3 条三），并在 assumptions 里回述
    clarify = json.dumps({"needClarification": True, "questions": [
        {"question": "金额含税吗", "default": "未税"}]})
    model_json = json.dumps(IR_INVENTORY)
    rounds = iter([clarify, model_json])
    result = extractor_with_content(rounds).extract("订单管理", answers=["就按默认的来"])
    assert result.needs_clarification is False
    assert result.model is not None
    assert any("默认值" in a or "USER" in a for a in result.assumptions)


def test_model_still_refuses_after_answers_is_an_error():
    content = json.dumps({"needClarification": True, "questions": []})
    rounds = iter([content, content])  # 强制建模后仍拒绝 → 报错请换说法
    with pytest.raises(schema_map.ModelValidationError):
        extractor_with_content(rounds).extract("订单管理", answers=["就按默认的来"])


def test_invalid_output_self_corrects_once():
    bad = json.dumps({"entities": [{"name": "Order", "columns": []}]})
    good = json.dumps(IR_INVENTORY)
    rounds = iter([bad, good])
    result = extractor_with_content(rounds).extract('库存管理')
    assert result.model is not None


def extractor_with_content(rounds):
    return RequirementExtractor(llm_call=lambda messages: next(rounds),
                                existing_entities={"trade_order"})


def test_duplicate_entity_reported():
    ir = json.loads(json.dumps(IR_INVENTORY))
    ir["entities"][0]["name"] = "trade_order"  # 与已有实体重名
    content = json.dumps(ir)
    rounds = iter([content, content])  # 自修正后仍重名 → 报错
    with pytest.raises(schema_map.ModelValidationError) as error:
        extractor_with_content(rounds).extract("又一个订单")
    assert any("重名" in p for p in error.value.problems)


def test_positive_number_semantic_gets_deterministic_constraint():
    """「必须为正数」写进语义但 LLM 漏了 constraint：映射层用规则兜住，不靠提示词祈祷。"""
    ir = {"entities": [{"name": "stock_in", "columns": [
        {"name": "quantity", "type": "INT", "semantic": "本次入库的商品数量，必须为正数"},
        {"name": "handler", "type": "VARCHAR", "length": 32, "semantic": "经办人"}]}]}
    model = schema_map.to_business_model(ir, "v3")
    constraints = model["entities"][0]["rules"]["constraints"]
    assert constraints == [{"field": "quantity", "op": "GT", "value": 0,
                            "message": "本次入库的商品数量，必须为正数（来自字段语义推断）"}]
