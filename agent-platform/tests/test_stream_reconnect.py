"""SSE 重连语义（审查 r5① 回归锚点）。

背景缺陷：重连连接的 follow 循环用 `task is None` 当「派发已完成」，
会在运行仍活跃时立即收殓（finish + 注入 done）——运行守卫被拆除，
同会话新消息可与旧 dispatch 并发双跑。

修复后的不变量：
- 收殓归 dispatch 生命周期（finally），连接生死不影响；
- 重连连接只读：不 finish、不向 hub 注入 done；
- 生成流的 dispatch 提前返回后，靠 gen_state 槽位跟到流水线尾部。

判别法：尾部事件延迟 0.2s 后才发生——有缺陷的实现会在那之前退出，
收不到尾部事件、或者守卫提前变 done。不使用 wait_for 取消探活
（超时取消会向异步生成器注入 CancelledError，污染后续断言）。

（仓库没有 pytest-asyncio，异步用例用 asyncio.run 包装成同步函数，零新依赖。）
"""

from __future__ import annotations

import asyncio

from syscraft.app import _follow_events
from syscraft.conversation.stream_hub import StreamHub


class FakeSession:
    """支持 gen_state 槽位读写的会话替身。"""

    def __init__(self, gen_state=None):
        self.gen_state = gen_state

    def get_slot(self, name):
        return self.gen_state if name == "gen_state" else None


def _hub_dones(hub, sid):
    return [e for e in hub.replay(sid) if e["type"] == "done"]


def test_reconnect_does_not_kill_active_run():
    """重连时运行仍活跃：保持跟随到派发收尾，不 finish、不注入 done。"""

    async def scenario():
        hub = StreamHub()
        hub.begin("s1")
        hub.emit("s1", {"type": "text", "text": "a"})
        session = FakeSession()  # 普通对话（非生成），dispatch 还在跑

        async def dispatch_tail():
            """模拟 dispatch 的剩余工作与 finally 收殓（延迟 0.2s）。"""
            await asyncio.sleep(0.2)
            assert hub.status("s1") == "running"  # 守卫此时必须还在（缺陷实现已拆掉）
            hub.emit("s1", {"type": "text", "text": "b"})
            hub.finish("s1")

        asyncio.create_task(dispatch_tail())
        events = [e async for e in _follow_events("s1", session, hub, None, after_seq=0)]
        # 缺陷实现在这里只会得到 ["a", "done"]——收不到尾部事件
        assert [(e["type"], e.get("text")) for e in events] == \
            [("text", "a"), ("text", "b"), ("done", None)]
        assert len(_hub_dones(hub, "s1")) == 0  # 重连连接没有向 hub 注入 done

    asyncio.run(scenario())


def test_reconnect_to_finished_run_replays_then_done():
    """重连已结束的运行：回放全部事件后立即 done（设计 6.2）。"""

    async def scenario():
        hub = StreamHub()
        hub.begin("s1")
        hub.emit("s1", {"type": "text", "text": "old"})
        hub.finish("s1")

        events = [e async for e in _follow_events("s1", FakeSession(), hub, None, after_seq=0)]
        assert [e["type"] for e in events] == ["text", "done"]
        assert hub.status("s1") == "done"  # 幂等：不产生额外状态变化

    asyncio.run(scenario())


def test_owner_finishes_normally():
    """owner 连接：dispatch 结束后回放残余并退出；收殓在 dispatch 身上，不双份。"""

    async def scenario():
        hub = StreamHub()
        hub.begin("s1")

        async def dispatch_body():
            hub.emit("s1", {"type": "text", "text": "x"})
            hub.finish("s1")

        task = asyncio.create_task(dispatch_body())
        events = [e async for e in _follow_events("s1", FakeSession(), hub, task, 0)]
        assert [e["type"] for e in events] == ["text", "done"]
        assert len(_hub_dones(hub, "s1")) == 0

    asyncio.run(scenario())


def test_owner_waits_for_generation_tail():
    """生成流：dispatch 已返回（gen_state=running），必须跟到流水线尾部收尾。"""

    async def scenario():
        hub = StreamHub()
        hub.begin("s1")
        task = asyncio.create_task(asyncio.sleep(0.01))
        session = FakeSession(gen_state="running")

        async def pipeline_tail():
            await asyncio.sleep(0.2)
            session.gen_state = None  # 流水线 finally 清槽位
            hub.emit("s1", {"type": "text", "text": "生成完成"})

        asyncio.create_task(pipeline_tail())
        events = [e async for e in _follow_events("s1", session, hub, task, 0)]
        # 缺陷实现会在 gen_state 还是 running 时就退出，收不到「生成完成」
        assert [(e["type"], e.get("text")) for e in events] == \
            [("text", "生成完成"), ("done", None)]

    asyncio.run(scenario())


def test_reconnect_during_generation_keeps_following():
    """重连时生成流水线仍在跑（hub 已被提前收殓）：不得提前退出。"""

    async def scenario():
        hub = StreamHub()
        hub.begin("s1")
        hub.emit("s1", {"type": "phase", "step": "③ 建表", "ok": True})
        hub.finish("s1")  # dispatch 的 finally 已收殓，但流水线还在跑
        session = FakeSession(gen_state="running")

        async def pipeline_tail():
            await asyncio.sleep(0.2)
            hub.emit("s1", {"type": "text", "text": "生成完成"})
            session.gen_state = None

        asyncio.create_task(pipeline_tail())
        events = [e async for e in _follow_events("s1", session, hub, None, after_seq=0)]
        assert [(e["type"], e.get("text")) for e in events] == \
            [("phase", None), ("text", "生成完成"), ("done", None)]

    asyncio.run(scenario())
