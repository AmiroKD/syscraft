"""事件中枢：seq 单调、回放、并发拒绝。"""

from __future__ import annotations

from syscraft.conversation.stream_hub import StreamHub


def test_begin_emit_replay_finish():
    hub = StreamHub()
    assert hub.begin("s1") is True
    hub.emit("s1", {"type": "thought", "text": "a"})
    hub.emit("s1", {"type": "table"})
    hub.emit("s1", {"type": "text", "text": "b"})
    hub.finish("s1")

    assert hub.is_active("s1") is False
    assert [e["seq"] for e in hub.replay("s1")] == [1, 2, 3]
    # 断线重连：只补缺失段
    assert [e["seq"] for e in hub.replay("s1", after_seq=2)] == [3]


def test_seq_monotonic_across_runs():
    """seq 跨轮递增不复位：重连的 after_seq 才能跨轮对齐。"""
    hub = StreamHub()
    hub.begin("s1")
    hub.emit("s1", {"type": "text"})
    hub.finish("s1")
    hub.begin("s1")  # 新一轮
    seq = hub.emit("s1", {"type": "text"})
    assert seq == 2


def test_begin_rejected_while_running():
    hub = StreamHub()
    assert hub.begin("s1") is True
    assert hub.begin("s1") is False  # 并发消息被拒（前端输入框禁用的后端约束）
    hub.finish("s1")
    assert hub.begin("s1") is True


def test_replay_unknown_session_empty():
    assert StreamHub().replay("nope") == []


def test_emit_after_finish_still_logged():
    """生成流的最终 text 事件在 finish 之前发出，回放必须能拿到。"""
    hub = StreamHub()
    hub.begin("s1")
    hub.emit("s1", {"type": "phase", "step": "x"})
    hub.emit("s1", {"type": "text", "text": "完成"})
    hub.finish("s1")
    kinds = [e["type"] for e in hub.replay("s1")]
    assert kinds == ["phase", "text"]
