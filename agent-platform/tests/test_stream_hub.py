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


def test_no_truncation_gap_long_run():
    """轮内不截断（审查 r5 留存项修复）：长流水线事件数超过旧上限 800，
    重连补发也不能缺中间事件。"""
    hub = StreamHub()
    hub.begin("s1")
    for i in range(1000):
        hub.emit("s1", {"type": "log", "text": str(i)})
    hub.finish("s1")
    replayed = hub.replay("s1", after_seq=400)
    assert len(replayed) == 600
    assert replayed[0]["seq"] == 401  # 无缺口：第一补发事件紧接 after_seq


def test_done_entries_swept_over_limit():
    """done 条目回收：超上限时从最老的 done 驱起，running 永不驱。"""
    from syscraft.conversation.stream_hub import MAX_TRACKED_SESSIONS
    hub = StreamHub()
    for i in range(MAX_TRACKED_SESSIONS):
        sid = f"s{i}"
        hub.begin(sid)
        hub.emit(sid, {"type": "text"})
        hub.finish(sid)
    assert len(hub._runs) == MAX_TRACKED_SESSIONS
    # 再开一个新会话 → 最老的 s0 被驱
    hub.begin("new")
    assert "s0" not in hub._runs
    assert len(hub._runs) == MAX_TRACKED_SESSIONS
    # running 条目不被驱：把所有条目变 done 之外的状态不行——直接验证
    hub2 = StreamHub()
    for i in range(MAX_TRACKED_SESSIONS + 5):
        sid = f"r{i}"
        hub2.begin(sid)          # 这些都保持 running
    hub2.finish("r0")            # 只有一个 done
    hub2.begin("extra")          # 超限 → 应驱 r0（唯一 done），running 全留
    assert "r0" not in hub2._runs
    assert all(f"r{i}" in hub2._runs for i in range(1, MAX_TRACKED_SESSIONS + 5))
