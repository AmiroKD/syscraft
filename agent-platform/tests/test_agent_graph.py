"""业务 Agent 图：工具循环、槽位指代消解、确认流（假 LLM + 假产物，不碰网络）。"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

import syscraft.agent.graph as graph_module
from syscraft.agent.graph import BusinessAgent
from syscraft.conversation.store import SessionStore
from syscraft.requirement.model_catalog import ModelCatalog

MODEL = {
    "entities": [{
        "entity": "trade_order", "display_name": "订单",
        "fields": [
            {"column": "id", "property": "id", "semantic": "主键", "type": "BIGINT"},
            {"column": "order_no", "property": "orderNo", "semantic": "订单号", "type": "VARCHAR"},
            {"column": "amount", "property": "amount", "semantic": "金额", "type": "DECIMAL"},
        ],
    }],
}


class FakeClient:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [
            {"id": 1, "order_no": "SO-1", "amount": 100.0},
            {"id": 2, "order_no": "SO-2", "amount": 200.0},
        ]
        self.calls: list[tuple] = []
        self.preview_tokens = ["tok-1"]

    async def query(self, entity, request, token):
        self.calls.append(("query", entity, request))
        rows = self.rows
        filter_ = request.get("filter")
        if filter_:
            for cond in filter_.get("conditions", []):
                if cond.get("operator") == "EQ":  # 假实现只认 EQ，够测试用
                    rows = [r for r in rows if str(r.get(cond["field"])) == str(cond.get("value"))]
        return {"rows": rows, "total": len(rows), "page": 1, "size": 20, "truncated": False}

    async def aggregate(self, entity, request, token):
        self.calls.append(("aggregate", entity, request))
        return [{"status": "PAID", "cnt": 2}]

    async def insert(self, entity, row, token):
        self.calls.append(("insert", entity, row))
        return 99

    async def update(self, entity, record, token):
        self.calls.append(("update", entity, record))
        return 1

    async def preview_delete(self, entity, ids, token):
        self.calls.append(("preview", entity, ids))
        return {"token": self.preview_tokens.pop(0),
                "summary": f"将删除「订单」 {len(ids)} 条（id: {ids}），令牌 5 分钟内有效"}

    async def delete(self, entity, ids, confirm_token, token):
        self.calls.append(("delete", entity, ids, confirm_token))
        return len(ids)


def tool_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"content": None, "tool_calls": [{"id": call_id, "name": name, "arguments": args}]}


def text_call(text: str) -> dict:
    return {"content": text, "tool_calls": []}


@pytest.fixture()
def catalog():
    # mkdtemp 代替 tmp_path：沙箱对复用临时根目录的 mkdir 有 EEXIST 怪癖（见 test_store）
    root = Path(tempfile.mkdtemp(prefix="syscraft-agent-test-"))
    models = root / "models"
    models.mkdir()
    (models / "trade_order.json").write_text(json.dumps(MODEL), encoding="utf-8")
    yield ModelCatalog.load(models)
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def store(catalog):
    root = Path(tempfile.mkdtemp(prefix="syscraft-agent-sess-"))
    yield SessionStore(root=root / "sessions")
    shutil.rmtree(root, ignore_errors=True)


def install_llm(monkeypatch, script: list[dict]):
    seen: list[list[dict]] = []

    async def fake(messages, tools):
        seen.append(messages)
        return script.pop(0)

    monkeypatch.setattr(graph_module, "chat_with_tools", fake)
    return seen


# ---------------------------------------------------------------- 查询流

@pytest.mark.anyio
async def test_query_flow_persists_messages_and_slots(monkeypatch, catalog, store):
    seen = install_llm(monkeypatch, [
        tool_call("query_records", {"entity": "trade_order", "filter": None}, "c1"),
        text_call("共 2 条订单。"),
    ])
    client = FakeClient()
    agent = BusinessAgent(client, catalog)
    session = store.create(user_id=1)

    final = await agent.run("查订单", "tk", session)

    assert final["reply"] == "共 2 条订单。"
    assert any(e["type"] == "table" and e["table"]["total"] == 2 for e in final["events"])
    # 消息落盘：user 原话 + assistant 表格 + assistant 文本
    roles = [(m["role"], m["type"]) for m in session.messages()]
    assert ("user", "text") in roles and ("assistant", "table") in roles
    # 槽位更新（设计 3.1）
    assert session.get_slot("lastEntity") == {"entity": "trade_order", "display": "订单"}
    assert session.get_slot("lastResultIds") == [1, 2]
    # LLM 收到的消息：system + user，且工具结果以 role=tool 回灌
    assert seen[0][0]["role"] == "system"
    assert any(m["role"] == "tool" for m in seen[1])


# ---------------------------------------------------------------- 指代消解

@pytest.mark.anyio
async def test_pronoun_resolved_via_last_entity_slot(monkeypatch, catalog, store):
    seen = install_llm(monkeypatch, [text_call("好的。")])
    agent = BusinessAgent(FakeClient(), catalog)
    session = store.create(user_id=1)
    session.set_slot("lastEntity", {"entity": "trade_order", "display": "订单"})

    await agent.run("它有多少条", "tk", session)
    last_user = seen[0][-1]["content"]
    assert "订单" in last_user and "它" not in last_user  # 指代被确定性替换


@pytest.mark.anyio
async def test_pronoun_without_slot_asks_back(monkeypatch, catalog, store):
    script = [text_call("不该被用到")]
    install_llm(monkeypatch, script)
    agent = BusinessAgent(FakeClient(), catalog)
    session = store.create(user_id=1)

    final = await agent.run("它有多少条", "tk", session)
    assert "哪张表" in final["reply"]
    assert script  # LLM 一次都没被调用——槽位为空就追问，不靠模型猜（设计 3.2 ③）


@pytest.mark.anyio
async def test_record_pronoun_single_id_injected(monkeypatch, catalog, store):
    seen = install_llm(monkeypatch, [text_call("好的。")])
    agent = BusinessAgent(FakeClient(), catalog)
    session = store.create(user_id=1)
    session.set_slot("lastEntity", {"entity": "trade_order", "display": "订单"})
    session.set_slot("lastResultIds", [7])

    await agent.run("把刚才那条删了", "tk", session)
    assert "id=7" in seen[0][-1]["content"]


@pytest.mark.anyio
async def test_record_pronoun_ambiguous_lists_candidates(monkeypatch, catalog, store):
    install_llm(monkeypatch, [text_call("不该被用到")])
    agent = BusinessAgent(FakeClient(), catalog)
    session = store.create(user_id=1)
    session.set_slot("lastResultIds", [7, 8])

    final = await agent.run("把刚才那条删了", "tk", session)
    assert "7" in final["reply"] and "说明要操作哪一条" in final["reply"]


# ---------------------------------------------------------------- 确认流

@pytest.mark.anyio
async def test_delete_only_previews_then_confirm_executes(monkeypatch, catalog, store):
    install_llm(monkeypatch, [
        tool_call("delete_records", {"entity": "trade_order", "filter": {
            "op": "AND", "conditions": [{"field": "order_no", "operator": "EQ", "value": "SO-1"}]}}, "c1"),
    ])
    client = FakeClient()
    agent = BusinessAgent(client, catalog)
    session = store.create(user_id=1)

    final = await agent.run("删除订单，订单号是SO-1", "tk", session)
    assert "确认" in final["reply"]
    assert ("delete", "trade_order", [1]) not in client.calls  # 只预告，未执行
    pending = session.get_slot("pending_confirm")
    assert pending and pending["ids"] == [1] and pending["token"] == "tok-1"

    # 用户确认（确定性路径，不过 LLM）
    final = await agent.run("确认", "tk", session)
    assert final["reply"] == "已删除 1 条。"
    assert ("delete", "trade_order", [1], "tok-1") in client.calls
    assert session.get_slot("pending_confirm") is None


@pytest.mark.anyio
async def test_confirm_without_pending(monkeypatch, catalog, store):
    """无待确认操作时确认词不短路——正常进 LLM（用户说「好的」是在对话，
    不是在核销；硬回「没有待确认的操作」会打断正常聊天）。"""
    install_llm(monkeypatch, [text_call("好的，还有什么需要查的？")])
    agent = BusinessAgent(FakeClient(), catalog)
    session = store.create(user_id=1)

    final = await agent.run("确认", "tk", session)
    assert "还有什么需要查的" in final["reply"]
    assert session.get_slot("pending_confirm") is None


# ---------------------------------------------------------------- 白名单与循环上限

@pytest.mark.anyio
async def test_field_hallucination_rejected_and_fed_back(monkeypatch, catalog, store):
    seen = install_llm(monkeypatch, [
        tool_call("query_records", {"entity": "trade_order", "filter": {
            "op": "AND", "conditions": [{"field": "user_id", "operator": "EQ", "value": 1}]}}, "c1"),
        text_call("那个字段不存在。"),
    ])
    agent = BusinessAgent(FakeClient(), catalog)
    session = store.create(user_id=1)

    final = await agent.run("查张三的订单", "tk", session)
    tool_message = next(m for m in seen[1] if m["role"] == "tool")
    assert json.loads(tool_message["content"])["ok"] is False
    assert "user_id" in json.loads(tool_message["content"])["error"]
    assert final["reply"] == "那个字段不存在。"


@pytest.mark.anyio
async def test_tool_round_limit_stops_loop(monkeypatch, catalog, store):
    # 模型每次都想再查一轮：到 MAX_TOOL_ROUNDS 就停，不无限兜圈
    script = [tool_call("query_records", {"entity": "trade_order"}, f"c{i}") for i in range(10)]
    seen = install_llm(monkeypatch, script)
    agent = BusinessAgent(FakeClient(), catalog)
    session = store.create(user_id=1)

    await agent.run("查订单", "tk", session)
    llm_turns = len(seen)
    assert llm_turns <= graph_module.MAX_TOOL_ROUNDS + 1
    assert len(script) > 0  # 脚本没用完——循环被上限切断


# ---------------------------------------------------------------- 工具白名单

@pytest.mark.anyio
async def test_unknown_entity_rejected(monkeypatch, catalog, store):
    seen = install_llm(monkeypatch, [
        tool_call("query_records", {"entity": "employee"}, "c1"),
        text_call("没有员工表。"),
    ])
    agent = BusinessAgent(FakeClient(), catalog)
    session = store.create(user_id=1)

    final = await agent.run("查员工", "tk", session)
    tool_message = next(m for m in seen[1] if m["role"] == "tool")
    assert "employee" in json.loads(tool_message["content"])["error"]
    assert final["reply"] == "没有员工表。"
