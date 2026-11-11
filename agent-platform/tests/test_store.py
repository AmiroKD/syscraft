"""会话存储：MySQL 落库、用户绑定、槽位、级联删除、列表排序。"""

from __future__ import annotations

import pytest

from syscraft.conversation.store import SessionNotFound, SessionStore


@pytest.fixture()
def store():
    # 库名由 conftest 固定为 chat_test，且每个用例前清表；这里只用默认构造
    return SessionStore()


def test_create_open_append_messages(store):
    session = store.create(user_id=1, title="查订单的事")
    assert session.id
    session.append("user", "text", "查订单")
    session.append("assistant", "table", {"entity": "trade_order", "total": 3},
                   tool_call={"name": "query_records", "args": {}})

    messages = session.messages()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "查订单"
    assert messages[1]["type"] == "table" and messages[1]["toolCall"]["name"] == "query_records"
    assert messages[0]["seq"] < messages[1]["seq"]


def test_messages_limit_returns_tail_in_order(store):
    session = store.create(user_id=1)
    for i in range(5):
        session.append("user", "text", f"第{i}条")
    tail = session.messages(limit=2)
    assert [m["content"] for m in tail] == ["第3条", "第4条"]


def test_user_binding_404_semantics(store):
    session = store.create(user_id=1)
    with pytest.raises(SessionNotFound):
        store.open(session.id, user_id=2)  # 换人访问 = 不存在，不暴露会话存在
    assert store.open(session.id, user_id=1).id == session.id


def test_path_traversal_rejected(store):
    with pytest.raises(SessionNotFound):
        store.open("../../etc/passwd", user_id=1)
    with pytest.raises(SessionNotFound):
        store.open("a;drop", user_id=1)


def test_slots_roundtrip_and_delete(store):
    session = store.create(user_id=1)
    session.set_slot("lastEntity", {"entity": "trade_order", "display": "订单"})
    assert session.get_slot("lastEntity") == {"entity": "trade_order", "display": "订单"}
    session.set_slot("lastEntity", None)
    assert session.get_slot("lastEntity") is None

    store.delete(session.id, user_id=1)
    with pytest.raises(SessionNotFound):
        store.open(session.id, user_id=1)


def test_delete_cascades_messages_and_slots(store):
    """删会话必须连带消息与槽位：靠外键 ON DELETE CASCADE，而不是靠调用方记得清。"""
    session = store.create(user_id=1)
    session.append("user", "text", "留着会占空间")
    session.set_slot("lastEntity", {"entity": "trade_order"})
    store.delete(session.id, user_id=1)

    with store._cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM chat_message WHERE session_id = %s", (session.id,))
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT COUNT(*) AS n FROM chat_slot WHERE session_id = %s", (session.id,))
        assert cur.fetchone()["n"] == 0


def test_rename_truncates_to_20_chars(store):
    session = store.create(user_id=1, title="旧标题")
    session.set_title("很长很长的标题" * 6)
    assert session.meta()["title"] == ("很长很长的标题" * 6)[:20]


def test_list_scoped_to_user_and_sorted(store):
    mine = [store.create(user_id=1) for _ in range(3)]
    theirs = store.create(user_id=2)
    listed = store.list_for(user_id=1)
    assert len(listed) == 3 and all(s["sessionId"] != theirs.id for s in listed)
    # 按 lastActiveAt 倒序
    stamps = [s["lastActiveAt"] for s in listed]
    assert stamps == sorted(stamps, reverse=True)
    assert mine  # silence unused


def test_missing_session_meta_raises(store):
    session = store.create(user_id=1)
    store.delete(session.id, user_id=1)
    with pytest.raises(SessionNotFound):
        session.meta()
