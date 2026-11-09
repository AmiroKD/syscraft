"""会话存储：文件制、用户绑定、槽位、物理删除。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from syscraft.conversation.store import SessionNotFound, SessionStore


@pytest.fixture()
def store():
    # 用 mkdtemp 而非 pytest 的 tmp_path：后者在复用临时根目录时会被本机沙箱的
    # mkdir 代理绊倒（EEXIST，RepairLoop 测试踩过），与被测代码无关
    root = Path(tempfile.mkdtemp(prefix="syscraft-store-test-"))
    yield SessionStore(root=root / "sessions")
    shutil.rmtree(root, ignore_errors=True)


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


def test_list_scoped_to_user_and_sorted(store):
    mine = [store.create(user_id=1) for _ in range(3)]
    theirs = store.create(user_id=2)
    listed = store.list_for(user_id=1)
    assert len(listed) == 3 and all(s["sessionId"] != theirs.id for s in listed)
    # 按 lastActiveAt 倒序
    stamps = [s["lastActiveAt"] for s in listed]
    assert stamps == sorted(stamps, reverse=True)
    assert mine  # silence unused


def test_corrupt_file_skipped_in_list(store):
    (store._root / "broken.db").write_bytes(b"not a sqlite file")
    store.create(user_id=1)
    assert len(store.list_for(user_id=1)) == 1  # 坏文件不进列表也不抛
