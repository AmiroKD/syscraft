"""事件中枢：每会话的事件日志与运行状态（内存态，进程生命周期一致）。

对应《会话与上下文策略》6.2 的断线续传：前端断线**不重发请求**（重发会产生两条
回答），带 seq 重连后服务端补发缺失事件；会话已结束则直接回放最终状态。

为什么在内存：运行中的任务本来就无法跨进程存活，平台重启后
「续传一次已死的运行」没有意义——回放日志只服务同一次运行内的断线重连。
seq 由中枢统一分配，单调递增；日志每轮 begin() 时重建，轮内不截断
（事件是小 dict，一轮最多几千个、几百 KB 量级，不值得用截断换内存——
截断会在长流水线上凿出回放缺口，重连拿不到中间事件又无从补救）。
done 条目不永久驻留：超上限时从最老的 done 驱起。"""

from __future__ import annotations

import asyncio
from collections import deque

MAX_TRACKED_SESSIONS = 100


class StreamHub:
    def __init__(self):
        self._runs: dict[str, dict] = {}

    # ---- 运行生命周期 ----

    def begin(self, session_id: str) -> bool:
        """登记一次运行（一条消息的派发）。已有运行在身时返回 False。

        seq 会话内单调递增、永不清零——重连的 after_seq 才能跨轮对齐；
        事件日志每轮清空，回放范围只限当前轮。"""
        run = self._runs.get(session_id)
        if run is not None and run["status"] == "running":
            return False
        self._runs[session_id] = {"status": "running",
                                  "seq": run["seq"] if run else 0,
                                  "events": deque(),
                                  "error": None}
        self._sweep()
        return True

    def _sweep(self) -> None:
        """done 条目回收（审查 r5 留存项）：done 的事件在下一次 begin 前还有
        重连回放价值，但不能无限堆——总会话数超限时从最老的 done 驱起，
        running 条目永远保留。"""
        if len(self._runs) <= MAX_TRACKED_SESSIONS:
            return
        done_ids = [sid for sid, run in self._runs.items() if run["status"] != "running"]
        overflow = len(self._runs) - MAX_TRACKED_SESSIONS
        for sid in done_ids[:overflow]:
            del self._runs[sid]

    def is_active(self, session_id: str) -> bool:
        return self.status(session_id) == "running"

    def emit(self, session_id: str, event: dict) -> int:
        run = self._runs.get(session_id)
        if run is None:
            return 0
        run["seq"] += 1
        logged = {"seq": run["seq"], **event}
        run["events"].append(logged)
        return run["seq"]

    def finish(self, session_id: str, error: str | None = None) -> None:
        run = self._runs.get(session_id)
        if run is not None:
            run["status"] = "done"
            run["error"] = error

    def status(self, session_id: str) -> str | None:
        run = self._runs.get(session_id)
        return run["status"] if run else None

    def replay(self, session_id: str, after_seq: int = 0) -> list[dict]:
        run = self._runs.get(session_id)
        if run is None:
            return []
        return [e for e in run["events"] if e["seq"] > after_seq]


# 全局生成锁（《会话与上下文策略》5 节：全局一把，持有者是 runtime，不是会话）
generation_lock = asyncio.Lock()
