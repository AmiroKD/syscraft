"""会话持久化：每会话一个 SQLite 文件（《会话与上下文策略》1.1）。

设计要点与理由：
- 不写 adm 库：违反 D3（Python 不写权限库），且会话不是权限数据；
- 不存 localStorage：刷新即丢、可被篡改；
- 每会话一个文件：天然避开单写锁竞争，删除会话 = 删文件，无残留。

安全边界（设计 7 节）：
- session_id 只允许 [A-Za-z0-9_-]，防路径穿越；
- 会话与 userId 绑定，换人访问按「不存在」处理（404 语义，不暴露会话存在）；
- 落盘的已是 Spring 裁剪后的数据（脱敏在产物侧完成），会话层不二次处理。
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT NOT NULL,
    type       TEXT NOT NULL,
    content    TEXT NOT NULL,
    tool_call  TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slots (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SessionNotFound(Exception):
    """会话不存在或不属于当前用户——调用方一律按 404 处理。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_session_id() -> str:
    return uuid.uuid4().hex[:16]


class Session:
    """单个会话的读写句柄。所有操作短连接：单循环 asyncio 下开销可忽略。"""

    def __init__(self, path: Path):
        self._path = path

    @property
    def id(self) -> str:
        return self._path.stem

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- 元信息 ----

    def meta(self) -> dict[str, Any]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM meta").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def touch(self) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE meta SET value=? WHERE key='last_active_at'", (_now(),))
            conn.commit()

    # ---- 消息 ----

    def append(self, role: str, type_: str, content: Any, tool_call: dict | None = None) -> int:
        """写一条消息。content 是结构（dict/str），落盘 JSON——存结构不存渲染结果。"""
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO messages (role, type, content, tool_call, created_at) VALUES (?,?,?,?,?)",
                (role, type_, json.dumps(content, ensure_ascii=False),
                 json.dumps(tool_call, ensure_ascii=False) if tool_call else None, _now()))
            conn.execute("UPDATE meta SET value=? WHERE key='last_active_at'", (_now(),))
            conn.commit()
            return int(cursor.lastrowid)

    def messages(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT seq, role, type, content, tool_call, created_at FROM messages ORDER BY seq"
        if limit:
            sql += f" DESC LIMIT {int(limit)}"
        with self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        result = [
            {"seq": row["seq"], "role": row["role"], "type": row["type"],
             "content": json.loads(row["content"]),
             "toolCall": json.loads(row["tool_call"]) if row["tool_call"] else None,
             "createdAt": row["created_at"]}
            for row in rows
        ]
        return list(reversed(result)) if limit else result

    # ---- 槽位（含待确认状态）----

    def get_slot(self, name: str) -> Any:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM slots WHERE name=?", (name,)).fetchone()
        return json.loads(row["value"]) if row else None

    def set_slot(self, name: str, value: Any) -> None:
        with self._conn() as conn:
            if value is None:
                conn.execute("DELETE FROM slots WHERE name=?", (name,))
            else:
                conn.execute(
                    "INSERT INTO slots (name, value) VALUES (?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                    (name, json.dumps(value, ensure_ascii=False)))
            conn.commit()


class SessionStore:
    """会话文件的根目录管理器。root 默认 workspace/sessions。"""

    def __init__(self, root: Path | str | None = None):
        self._root = Path(root) if root else _default_root()
        self._root.mkdir(parents=True, exist_ok=True)

    def create(self, user_id: int, title: str = "新会话") -> Session:
        session_id = new_session_id()
        path = self._root / f"{session_id}.db"
        with sqlite3.connect(path) as conn:
            conn.executescript(_SCHEMA)
            now = _now()
            conn.executemany(
                "INSERT INTO meta (key, value) VALUES (?,?)",
                [("user_id", str(user_id)), ("title", title[:20]),
                 ("mode", "BUSINESS"), ("created_at", now), ("last_active_at", now)])
            conn.commit()
        return Session(path)

    def open(self, session_id: str, user_id: int) -> Session:
        """打开会话；不存在或不属于该用户都抛 SessionNotFound（404 语义）。"""
        if not SESSION_ID_RE.match(session_id or ""):
            raise SessionNotFound()
        path = self._root / f"{session_id}.db"
        if not path.exists():
            raise SessionNotFound()
        session = Session(path)
        if session.meta().get("user_id") != str(user_id):
            raise SessionNotFound()
        return session

    def list_for(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        """侧栏列表：按最近活跃倒序，最多 limit 条（设计 4 节）。"""
        items: list[dict[str, Any]] = []
        for path in self._root.glob("*.db"):
            try:
                meta = Session(path).meta()
            except sqlite3.DatabaseError:
                continue  # 半成品文件（写一半崩了）不进列表
            if meta.get("user_id") != str(user_id):
                continue
            items.append({"sessionId": path.stem, "title": meta.get("title", ""),
                          "lastActiveAt": meta.get("last_active_at", "")})
        items.sort(key=lambda item: item["lastActiveAt"], reverse=True)
        return items[:limit]

    def delete(self, session_id: str, user_id: int) -> None:
        session = self.open(session_id, user_id)
        # 设计 1.1：删除即物理删除文件，不留副本（审计在 adm_audit，不受影响）
        session._path.unlink(missing_ok=True)


def _default_root() -> Path:
    from syscraft import config
    return config.PROJECT_ROOT / "workspace" / "sessions"
