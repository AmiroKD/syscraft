"""暂停与刷新恢复（《暂停与刷新恢复设计》4 节）。

两条不变量：
- 暂停是**取消本轮任务**，不是挂起：cancel 之后必须由收殓把「用户已看见的表格」
  和一句 notice 落库，否则暂停后刷新会看到表格消失、也不知道这轮为什么断在半路；
- 刷新后的接管是**只读跟随**：resume 连接不得派发新消息，也不得拆掉运行守卫
  （守卫语义见 test_stream_reconnect）。

（仓库没有 pytest-asyncio，异步用例用 asyncio.run 包装成同步函数，零新依赖。）
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import syscraft.app as app_module
from syscraft.app import PAUSE_NOTE, _persist_live_table, _settle_pause, _stream_decision
from syscraft.conversation.stream_hub import StreamHub


class FakeSession:
    """会话替身：记录落库顺序，history 用来验证表格去重。"""

    def __init__(self, history=None, session_id="s1"):
        self.id = session_id
        self.appended: list[dict] = []
        self.history = list(history or [])

    def append(self, role, type_, content, tool_call=None):
        self.appended.append({"role": role, "type": type_, "content": content,
                              "tool_call": tool_call})
        return len(self.appended)

    def messages(self, limit=None):
        return self.history[-limit:] if limit else list(self.history)

    def get_slot(self, name):
        return None


def _table_event(seq=0):
    return {"type": "table", "replace": True, "table": {
        "entity": "activity", "display": "活动",
        "columns": [{"key": "id", "title": "ID", "type": "long"}],
        "rows": [{"id": 1}], "total": 1,
        "toolCall": {"name": "query_records", "args": {}}}}


# ---------------------------------------------------------------- 中枢语义

def test_request_pause_cancels_dispatch_task():
    async def scenario():
        hub = StreamHub()
        hub.begin("s1")

        async def dispatch():
            await asyncio.sleep(30)

        task = asyncio.create_task(dispatch())
        hub.attach_task("s1", task)
        assert hub.request_pause("s1") is True
        assert hub.is_paused("s1") is True
        await asyncio.sleep(0)  # 让 cancel 落地
        assert task.cancelled() is True

    asyncio.run(scenario())


def test_request_pause_without_running_run_rejected():
    hub = StreamHub()
    assert hub.request_pause("nope") is False
    hub.begin("s1")
    hub.finish("s1")
    # 已收殓的运行不再受理暂停：否则会去 cancel 一个早就结束的任务
    assert hub.request_pause("s1") is False


def test_begin_resets_pause_flag():
    hub = StreamHub()
    hub.begin("s1")
    hub.request_pause("s1")
    hub.finish("s1")
    assert hub.begin("s1") is True
    assert hub.is_paused("s1") is False


def test_seq_survives_finish_for_resume():
    """刷新接管要按 seq 续上：运行结束也不能把序号清零。"""
    hub = StreamHub()
    hub.begin("s1")
    hub.emit("s1", {"type": "text", "text": "a"})
    hub.emit("s1", {"type": "text", "text": "b"})
    hub.finish("s1")
    assert hub.seq("s1") == 2


# ---------------------------------------------------------------- 收殓

def test_settle_pause_persists_table_then_notice():
    hub = StreamHub()
    hub.begin("s1")
    hub.emit("s1", {"type": "thought", "text": "调用 query_records"})
    hub.emit("s1", _table_event())
    session = FakeSession()

    _settle_pause("s1", session, hub)

    assert [m["type"] for m in session.appended] == ["table", "notice"]
    assert session.appended[0]["content"]["display"] == "活动"
    assert session.appended[1]["content"] == PAUSE_NOTE
    paused = [e for e in hub.replay("s1") if e["type"] == "paused"]
    assert paused and paused[0]["text"] == PAUSE_NOTE


def test_settle_pause_is_silent_when_answer_already_emitted():
    """回答已经发出来了（暂停来晚一步）：不再补「已暂停」的说明，免得像失败。"""
    hub = StreamHub()
    hub.begin("s1")
    hub.emit("s1", {"type": "text", "text": "共 3 条活动。"})
    session = FakeSession()

    _settle_pause("s1", session, hub)

    assert session.appended == []
    assert [e["type"] for e in hub.replay("s1")] == ["text"]


def test_persist_live_table_dedups_same_entity():
    """上一轮已落过同实体的表格：暂停收殓不再写第二张（多轮只留最终表）。"""
    session = FakeSession(history=[{"type": "table", "content": {"entity": "activity"}}])
    _persist_live_table(session, _table_event()["table"])
    assert session.appended == []


def test_persist_live_table_truncates_rows():
    from syscraft.agent.graph import MAX_HISTORY_ROWS
    table = _table_event()["table"] | {"rows": [{"id": i} for i in range(MAX_HISTORY_ROWS + 20)]}
    session = FakeSession()
    _persist_live_table(session, table)
    assert len(session.appended[0]["content"]["rows"]) == MAX_HISTORY_ROWS
    assert session.appended[0]["content"]["displayed"] == MAX_HISTORY_ROWS


# ---------------------------------------------------------------- 流决策

def test_stream_decision_matrix():
    # 会话空闲、无生成锁 → 派发新消息
    assert _stream_decision(False, False, False) == "start"
    # 另一个会话在生成 → 拒绝（全局锁）
    assert _stream_decision(False, False, True) == "generation_locked"
    # 本会话在跑：新消息 409，续传跟随
    assert _stream_decision(True, False, False) == "conflict"
    assert _stream_decision(True, True, False) == "follow"
    # 运行已结束的续传：回放后 done
    assert _stream_decision(False, True, False) == "follow"


# ---------------------------------------------------------------- HTTP 端点

class _FakeStore:
    def __init__(self, session):
        self._session = session

    def open(self, session_id, user_id):  # noqa: ARG002 会话归属由真实 store 校验
        return self._session


@pytest.fixture
def client():
    return TestClient(app_module.app)


def _stub(monkeypatch, hub=None, session=None):
    """把身份、会话、事件中枢全部换成替身——端点测的是分支，不是数据库。"""

    async def fake_me(token):  # noqa: ARG001
        return {"id": 1, "username": "admin", "roleCode": "ADMIN", "roleName": "管理员"}

    monkeypatch.setattr(app_module._client, "me", fake_me)
    monkeypatch.setattr(app_module.config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(app_module, "_hub", hub or StreamHub())
    monkeypatch.setattr(app_module, "_sessions", _FakeStore(session or FakeSession()))


def test_pause_endpoint_without_running_run_is_409(client, monkeypatch):
    _stub(monkeypatch)
    resp = client.post("/api/chat/pause", json={"session_id": "s1"},
                       headers={"Authorization": "Bearer t"})
    assert resp.status_code == 409
    assert "没有进行中的回答" in resp.json()["msg"]


def test_pause_endpoint_marks_run(client, monkeypatch):
    hub = StreamHub()
    hub.begin("s1")
    _stub(monkeypatch, hub=hub)
    resp = client.post("/api/chat/pause", json={"session_id": "s1"},
                       headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    assert resp.json()["data"]["pausing"] is True
    assert hub.is_paused("s1") is True


def test_pause_endpoint_refuses_during_generation(client, monkeypatch):
    """生成流水线跑在 to_thread 里，取消任务停不了它——必须指回「取消生成」。"""
    hub = StreamHub()
    hub.begin("s1")
    session = FakeSession()
    session.get_slot = lambda name: "running" if name == "gen_state" else None
    _stub(monkeypatch, hub=hub, session=session)
    resp = client.post("/api/chat/pause", json={"session_id": "s1"},
                       headers={"Authorization": "Bearer t"})
    assert resp.status_code == 409
    assert "取消生成" in resp.json()["msg"]
    assert hub.is_paused("s1") is False


def test_resume_stream_replays_finished_run(client, monkeypatch):
    """刷新后接管：不带消息、带 resume=1，回放既有事件后 done（不重发请求）。"""
    hub = StreamHub()
    hub.begin("s1")
    hub.emit("s1", {"type": "text", "text": "共 3 条"})
    hub.finish("s1")
    _stub(monkeypatch, hub=hub)

    resp = client.post("/api/chat/stream?after_seq=0&resume=1", json={"session_id": "s1"},
                       headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    assert '"text"' in resp.text and '"done"' in resp.text


def test_stream_rejects_empty_message_and_resume_without_session(client, monkeypatch):
    _stub(monkeypatch)
    empty = client.post("/api/chat/stream", json={"message": "   "},
                        headers={"Authorization": "Bearer t"})
    assert empty.status_code == 400
    no_session = client.post("/api/chat/stream?resume=1", json={},
                             headers={"Authorization": "Bearer t"})
    assert no_session.status_code == 400

