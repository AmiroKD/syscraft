"""事件日志（《代码生成器设计》6.3/6.9）。

存储不压缩：全量 JSONL 落盘，含动作参数、结果与 diff。上下文裁剪是投影的事（projector），
日志自己永远完整——可回放、可续跑、答辩时可逐轮展示。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Event:
    seq: int
    type: str
    payload: dict = field(default_factory=dict)
    ts: str = field(default_factory=_now)

    def summary(self) -> str:
        """一行摘要，给投影的远端压缩用。"""
        ok = self.payload.get("ok")
        brief = self.payload.get("brief") or self.payload.get("path") or self.payload.get("tool") or ""
        return f"#{self.seq} {self.type} {'ok' if ok else 'fail'} {brief}".strip()


class EventLog:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._events: list[Event] = []
        self._seq = 0

    def append(self, event_type: str, **payload) -> Event:
        self._seq += 1
        event = Event(seq=self._seq, type=event_type, payload=payload)
        self._events.append(event)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "seq": event.seq, "type": event.type, "ts": event.ts, **payload,
            }, ensure_ascii=False) + "\n")
        return event

    def all(self) -> list[Event]:
        return list(self._events)

    def last(self, event_type: str) -> Event | None:
        for event in reversed(self._events):
            if event.type == event_type:
                return event
        return None
