"""编排层：查询请求形态、表格映射、回复文案、错误翻译、删除确认流程。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from syscraft.conversation.chat import ChatService, build_query
from syscraft.conversation.session import PendingConfirm, SessionStore
from syscraft.requirement.intent import RuleBasedIntentResolver
from syscraft.tools.spring_client import SpringError


def run(coro):
    return asyncio.run(coro)


QUERY_OK = {"rows": [], "total": 0, "page": 1, "size": 20, "truncated": False}


class FakeClient:
    """实现 query / preview_delete / delete，带调用记录。"""

    def __init__(self, query_payload=None, ticket=None, deleted_rows=1, query_error=None):
        self.query_payload = query_payload or QUERY_OK
        self.ticket = ticket
        self.deleted_rows = deleted_rows
        self.query_error = query_error
        self.calls = {"query": [], "preview": [], "delete": []}

    async def query(self, entity, request, token):
        self.calls["query"].append((entity, request, token))
        if self.query_error:
            raise self.query_error
        return self.query_payload

    async def preview_delete(self, entity, ids, token):
        self.calls["preview"].append((entity, list(ids), token))
        if self.ticket is None:
            raise SpringError(500, None, "未配置预告")
        return self.ticket

    async def delete(self, entity, ids, confirm_token, token):
        self.calls["delete"].append((entity, list(ids), confirm_token, token))
        return self.deleted_rows


def ticket(token="tok-1", impact=1):
    return {"token": token, "impactRows": impact,
            "summary": f"将删除「订单」 {impact} 条（id: 7），令牌 5 分钟内有效",
            "expiresAt": "2026-11-09T19:00:00"}


# ------------------------------------------------------------------ 查询

def test_build_query_shape():
    resolver = RuleBasedIntentResolver()
    intent = resolver.resolve("查订单 状态是CREATED 前5条")
    query = build_query(intent)
    assert query["filter"] == {"op": "AND", "conditions": [
        {"field": "status", "operator": "EQ", "value": "CREATED"}]}
    assert query["size"] == 5
    assert query["orderBy"] == [{"field": "created_at", "direction": "DESC"}]


def test_reply_reports_total_and_sum():
    payload = {"rows": [{"id": 1, "order_no": "SO-1", "amount": 100.0},
                        {"id": 2, "order_no": "SO-2", "amount": 250.5}],
               "total": 2, "page": 1, "size": 20, "truncated": False}
    service = ChatService(FakeClient(payload), RuleBasedIntentResolver())
    reply = run(service.handle("查订单", "tk"))
    assert "共 2 条订单" in reply.reply
    assert "350.50" in reply.reply


def test_partial_page_says_page_total_not_grand_total():
    # 只说「金额合计」会把本页合计当成全量，用户据此对账会差钱
    payload = {"rows": [{"id": 1, "order_no": "SO-1", "amount": 100.0}],
               "total": 3, "page": 1, "size": 1, "truncated": False}
    service = ChatService(FakeClient(payload), RuleBasedIntentResolver())
    reply = run(service.handle("查订单 前1条", "tk"))
    assert "已显示前 1 条" in reply.reply
    assert "本页金额合计" in reply.reply


def test_forbidden_is_explained_not_leaked():
    service = ChatService(FakeClient(query_error=SpringError(403, 403, "无此操作权限：biz:trade_order:read")),
                          RuleBasedIntentResolver())
    reply = run(service.handle("查订单", "tk"))
    assert reply.reply == "当前账号没有查看「订单」的权限。"


# ------------------------------------------------------------------ 删除预告

def test_delete_flow_previews_and_asks_confirmation():
    payload = {"rows": [{"id": 7, "order_no": "SO-1", "amount": None}],
               "total": 1, "page": 1, "size": 200, "truncated": False}
    client = FakeClient(payload, ticket=ticket())
    sessions = SessionStore()
    service = ChatService(client, RuleBasedIntentResolver(), sessions)

    reply = run(service.handle("删除订单，订单号是SO-1", "tk", "s1"))
    assert "将删除「订单」 1 条" in reply.reply
    assert "确认请回复「确认」" in reply.reply
    # 预告用的查询会带上过滤条件
    assert client.calls["query"][0][1]["filter"]["conditions"][0]["field"] == "order_no"
    assert client.calls["preview"] == [("trade_order", [7], "tk")]


def test_delete_without_match_asks_back():
    service = ChatService(FakeClient(), RuleBasedIntentResolver())
    reply = run(service.handle("删除订单，订单号是SO-X", "tk"))
    assert reply.reply == "没有找到符合条件的订单。"


def test_delete_too_many_is_refused():
    # 删 500 条的预告文案谁都不会细看；确认的意义就是让人看清影响面
    payload = {"rows": [{"id": i} for i in range(200)],
               "total": 500, "page": 1, "size": 200, "truncated": True}
    service = ChatService(FakeClient(payload), RuleBasedIntentResolver())
    reply = run(service.handle("删除订单，状态是CREATED", "tk"))
    assert "超出单次删除上限" in reply.reply
    assert "缩小范围" in reply.reply


# ------------------------------------------------------------------ 确认执行

def ready_service(client, session_id="s1", token="tk"):
    sessions = SessionStore()
    service = ChatService(client, RuleBasedIntentResolver(), sessions)
    run(service.handle("删除订单，订单号是SO-1", token, session_id))
    return service, sessions


def test_confirmation_executes_delete_with_token():
    client = FakeClient({"rows": [{"id": 7}], "total": 1, "page": 1, "size": 200, "truncated": False},
                        ticket=ticket("tok-1"))
    service, _ = ready_service(client)
    reply = run(service.handle("确认", "tk", "s1"))
    assert reply.reply == "已删除 1 条。"
    assert client.calls["delete"] == [("trade_order", [7], "tok-1", "tk")]


def test_confirmation_consumes_pending():
    client = FakeClient({"rows": [{"id": 7}], "total": 1, "page": 1, "size": 200, "truncated": False},
                        ticket=ticket())
    service, sessions = ready_service(client)
    run(service.handle("确认", "tk", "s1"))
    # 核销后待确认状态必须清掉，否则第二次「确认」会拿旧令牌撞 409
    assert sessions.pop("s1") is None


def test_confirmation_without_pending():
    service = ChatService(FakeClient(), RuleBasedIntentResolver())
    reply = run(service.handle("确认", "tk"))
    assert reply.reply == "当前没有待确认的操作。"


def test_expired_pending_is_treated_as_absent():
    sessions = SessionStore()
    sessions.put("s1", PendingConfirm(token="t", entity="trade_order", ids=(1,), summary="s",
                                      expires_at=datetime.now() - timedelta(seconds=1)))
    service = ChatService(FakeClient(), RuleBasedIntentResolver(), sessions)
    reply = run(service.handle("确认", "tk", "s1"))
    assert reply.reply == "当前没有待确认的操作。"


def test_conflict_on_execute_prompts_retry():
    client = FakeClient({"rows": [{"id": 7}], "total": 1, "page": 1, "size": 200, "truncated": False},
                        ticket=ticket())
    service, _ = ready_service(client)

    async def fail_delete(*args, **kwargs):
        raise SpringError(409, 409, "确认令牌已被使用或已过期")

    client.delete = fail_delete
    reply = run(service.handle("确认", "tk", "s1"))
    assert "确认令牌已失效" in reply.reply
    assert "重新发起" in reply.reply


def test_token_is_passed_through_delete_chain():
    client = FakeClient({"rows": [{"id": 7}], "total": 1, "page": 1, "size": 200, "truncated": False},
                        ticket=ticket("tok-9"))
    service, _ = ready_service(client, "s2", token="tk-ops")
    run(service.handle("确认", "tk-ops", "s2"))
    assert client.calls["preview"][0][2] == "tk-ops"
    assert client.calls["delete"][0][3] == "tk-ops"
