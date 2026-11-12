"""对话会话的进程内状态。

只存一件事：一条待确认的删除（令牌 + 影响范围）。令牌本身 5 分钟过期，
这里的 TTL 与之对齐——存储活得比令牌久没有意义。

不做持久化：重启后待确认状态丢失，用户重新说一遍即可。
代价是重启后「确认」两个字没人接得住，收益是省一整张表和清理任务，
这笔账在第一周是划算的（会话持久化属于会话层，用 SqliteSaver 时再迁）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True)
class PendingConfirm:
    user_id: int
    token: str
    entity: str
    ids: tuple[int, ...]
    summary: str
    expires_at: datetime = field(compare=False)

    def expired(self) -> bool:
        return datetime.now() >= self.expires_at


class SessionStore:
    """key = session_id。单进程足够：平台目前单实例，多实例时换 Redis 再说。

    待确认状态必须绑定发起人：session_id 是客户端可传的字符串，不能当身份用——
    不绑定的话，B 带上同一个 session_id 回复「确认」就能核销 A 发起的删除。"""

    def __init__(self) -> None:
        self._pending: dict[str, PendingConfirm] = {}
        self._lock = threading.Lock()

    def put(self, session_id: str | None, pending: PendingConfirm) -> None:
        with self._lock:
            self._pending[self._key(session_id)] = pending

    def pop(self, session_id: str | None, user_id: int | None) -> PendingConfirm | None:
        """取出并清除。过期的当作不存在——过期满 5 分钟的令牌核销必然 409，
        与其让用户看到一句数据库味的报错，不如现在就请他重新说一遍。

        user_id 不匹配（或缺失）按不存在处理：不取走、不泄露「这里有一个
        待确认操作」——否则冒用者能把真正发起人的确认机会挤掉。"""
        if user_id is None:
            return None
        key = self._key(session_id)
        with self._lock:
            pending = self._pending.get(key)
            if pending is None or pending.user_id != user_id:
                return None
            del self._pending[key]
        if pending.expired():
            return None
        return pending

    @staticmethod
    def _key(session_id: str | None) -> str:
        return session_id or "__default__"
