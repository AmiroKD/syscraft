"""编排层：查询请求的形态、表格映射、回复文案、错误翻译。"""

from __future__ import annotations

import asyncio

import pytest

from syscraft.conversation.chat import ChatService, build_query, to_table
from syscraft.requirement.intent import RuleBasedIntentResolver
from syscraft.requirement.model_catalog import catalog
from syscraft.tools.spring_client import SpringError


def run(coro):
    return asyncio.run(coro)


class FakeClient:
    """只实现 query：编排层不该依赖登录与聚合。"""

    def __init__(self, payload=None, error: SpringError | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, dict, str]] = []

    async def query(self, entity, request, token):
        self.calls.append((entity, request, token))
        if self.error:
            raise self.error
        return self.payload


def test_build_query_shape():
    resolver = RuleBasedIntentResolver()
    intent = resolver.resolve("查订单 状态是CREATED 前5条")
    query = build_query(intent)
    assert query["filter"] == {"op": "AND", "conditions": [
        {"field": "status", "operator": "EQ", "value": "CREATED"}]}
    assert query["size"] == 5
    assert query["orderBy"] == [{"field": "created_at", "direction": "DESC"}]


def test_no_condition_means_null_filter():
    resolver = RuleBasedIntentResolver()
    assert build_query(resolver.resolve("查订单"))["filter"] is None


def test_reply_reports_total_and_sum():
    payload = {
        "rows": [
            {"id": 1, "order_no": "SO-1", "amount": 100.0},
            {"id": 2, "order_no": "SO-2", "amount": 250.5},
        ],
        "total": 2, "page": 1, "size": 20, "truncated": False,
    }
    service = ChatService(FakeClient(payload), RuleBasedIntentResolver())
    reply = run(service.handle("查订单", "tk"))
    assert "共 2 条订单" in reply.reply
    assert "350.50" in reply.reply
    assert reply.table is not None and len(reply.table.rows) == 2


def test_table_columns_use_chinese_titles():
    entity = catalog().by_name("trade_order")
    table = to_table(entity, {"rows": [], "total": 0, "page": 1, "size": 20, "truncated": False})
    titles = [c.title for c in table.columns]
    assert "订单号" in titles and "金额" in titles


def test_empty_result_has_plain_words():
    service = ChatService(FakeClient({"rows": [], "total": 0, "page": 1, "size": 20, "truncated": False}),
                          RuleBasedIntentResolver())
    reply = run(service.handle("查订单", "tk"))
    assert reply.reply == "没有查到订单。"


def test_forbidden_is_explained_not_leaked():
    service = ChatService(FakeClient(error=SpringError(403, 403, "无此操作权限：biz:trade_order:read")),
                          RuleBasedIntentResolver())
    reply = run(service.handle("查订单", "tk"))
    assert reply.reply == "当前账号没有查看「订单」的权限。"
    assert reply.table is None


def test_unreachable_product_is_reported():
    service = ChatService(FakeClient(error=SpringError(503, None, "产物服务不可达")), RuleBasedIntentResolver())
    reply = run(service.handle("查订单", "tk"))
    assert "查询失败" in reply.reply


def test_token_is_passed_through():
    client = FakeClient({"rows": [], "total": 0, "page": 1, "size": 20, "truncated": False})
    run(ChatService(client, RuleBasedIntentResolver()).handle("查订单", "tok-123"))
    assert client.calls[0][2] == "tok-123"


def test_partial_page_says_page_total_not_grand_total():
    # 只说「金额合计」会把本页合计当成全量，用户据此对账会差钱
    payload = {
        "rows": [{"id": 1, "order_no": "SO-1", "amount": 100.0}],
        "total": 3, "page": 1, "size": 1, "truncated": False,
    }
    service = ChatService(FakeClient(payload), RuleBasedIntentResolver())
    reply = run(service.handle("查订单 前1条", "tk"))
    assert "已显示前 1 条" in reply.reply
    assert "本页金额合计" in reply.reply
