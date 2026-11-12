"""会话持久化：MySQL 独立库 `chat`（《会话与上下文策略》1.1）。

为什么是独立库，而不是 adm / biz：
- **不写 adm**：Python 不碰权限库（禁令 D3），会话也不是权限数据；
- **不混 biz**：会话是平台的编排状态（槽位里是 gen_state、待确认令牌），
  混进业务库会污染业务备份、导出与行数统计；
- 于是单开 `chat` 库，并给它一个只授权 `chat.*` 的账号——「平台只写自己的库」
  不靠约定的自觉，而由数据库权限兜住（越权写 adm 会直接 Access denied）。

为什么不放进程内的字典：刷新/重启即丢，会话要跨重启恢复；也不放 localStorage：
可篡改、业务数据留在客户端。

连接策略：每次操作短连接、autocommit。本机会话读写在毫秒级，池化收益不抵复杂度；
真正的并发压力在产物侧（Spring），不在这里。**注意这是同步调用**，FastAPI 的
事件循环里直接执行会有毫秒级阻塞，单用户演示语义内可接受，多用户要改 to_thread。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from syscraft import config

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS chat_session (
        id             VARCHAR(32)     NOT NULL,
        user_id        BIGINT UNSIGNED NOT NULL COMMENT '会话属主；换人访问按不存在处理',
        title          VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '首条消息前 20 字，可重命名',
        mode           VARCHAR(16)     NOT NULL DEFAULT 'BUSINESS',
        created_at     DATETIME        NOT NULL,
        last_active_at DATETIME        NOT NULL,
        PRIMARY KEY (id),
        KEY idx_chat_session_user (user_id, last_active_at)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci
      COMMENT = '对话会话'
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_message (
        seq        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        session_id VARCHAR(32)     NOT NULL,
        `role`     VARCHAR(16)     NOT NULL,
        `type`     VARCHAR(24)     NOT NULL COMMENT 'text/table/plan/confirm/error…',
        content    LONGTEXT        NOT NULL COMMENT 'JSON 结构，不是渲染后的 HTML',
        tool_call  LONGTEXT        NULL,
        created_at DATETIME        NOT NULL,
        PRIMARY KEY (seq),
        KEY idx_chat_message_session (session_id, seq),
        CONSTRAINT fk_chat_message_session FOREIGN KEY (session_id)
            REFERENCES chat_session (id) ON DELETE CASCADE
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci
      COMMENT = '会话消息'
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_slot (
        session_id VARCHAR(32) NOT NULL,
        name       VARCHAR(32) NOT NULL COMMENT '指代消解槽位 / 生成状态',
        value      LONGTEXT    NOT NULL COMMENT 'JSON',
        updated_at DATETIME    NOT NULL,
        PRIMARY KEY (session_id, name),
        CONSTRAINT fk_chat_slot_session FOREIGN KEY (session_id)
            REFERENCES chat_session (id) ON DELETE CASCADE
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci
      COMMENT = '会话槽位'
    """,
)


class SessionNotFound(Exception):
    """会话不存在或不属于当前用户——调用方一律按 404 处理。"""


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


# gen_state（生成状态槽位）的进程内镜像：SSE 跟随循环每 0.15s 轮询它，
# 不能把每条连接的轮询都变成一次 MySQL 查询。写穿透 + 读回填，
# 进程生命周期内镜像即真相（重启后流水线已死，槽位残值由 handle() 复位）
_GEN_STATE_MEM: dict[str, Any] = {}


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def new_session_id() -> str:
    return uuid.uuid4().hex[:16]


class Session:
    """单个会话的读写句柄。所有方法都是一次短连接 + 一条语句。"""

    def __init__(self, store: "SessionStore", session_id: str):
        self._store = store
        self._id = session_id

    @property
    def id(self) -> str:
        return self._id

    # ---- 元信息 ----

    def meta(self) -> dict[str, Any]:
        with self._store._cursor() as cur:
            cur.execute(
                "SELECT user_id, title, mode, created_at, last_active_at "
                "FROM chat_session WHERE id = %s", (self._id,))
            row = cur.fetchone()
        if row is None:
            raise SessionNotFound()
        return {
            "user_id": str(row["user_id"]),  # 保持旧实现的字符串语义，调用方按字符串比较
            "title": row["title"],
            "mode": row["mode"],
            "created_at": _iso(row["created_at"]),
            "last_active_at": _iso(row["last_active_at"]),
        }

    def touch(self) -> None:
        with self._store._cursor() as cur:
            cur.execute("UPDATE chat_session SET last_active_at = %s WHERE id = %s",
                        (_now(), self._id))

    def set_title(self, title: str) -> None:
        with self._store._cursor() as cur:
            cur.execute("UPDATE chat_session SET title = %s WHERE id = %s",
                        (title[:20], self._id))

    # ---- 消息 ----

    def append(self, role: str, type_: str, content: Any, tool_call: dict | None = None) -> int:
        """写一条消息。content 落 JSON——存结构不存渲染结果，前端重开时重新渲染。"""
        now = _now()
        with self._store._cursor() as cur:
            cur.execute(
                "INSERT INTO chat_message (session_id, `role`, `type`, content, tool_call, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (self._id, role, type_, json.dumps(content, ensure_ascii=False),
                 json.dumps(tool_call, ensure_ascii=False) if tool_call else None, now))
            seq = int(cur.lastrowid)
            cur.execute("UPDATE chat_session SET last_active_at = %s WHERE id = %s", (now, self._id))
        return seq

    def messages(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT seq, `role`, `type`, content, tool_call, created_at "
               "FROM chat_message WHERE session_id = %s ORDER BY seq")
        params: tuple[Any, ...] = (self._id,)
        if limit:
            sql += " DESC LIMIT %s"  # int() 化后再拼，LIMIT 不接受占位符以外的表达式
            params += (int(limit),)
        with self._store._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        result = [
            {"seq": row["seq"], "role": row["role"], "type": row["type"],
             "content": json.loads(row["content"]),
             "toolCall": json.loads(row["tool_call"]) if row["tool_call"] else None,
             "createdAt": _iso(row["created_at"])}
            for row in rows
        ]
        return list(reversed(result)) if limit else result

    # ---- 槽位（含待确认状态）----

    def get_slot(self, name: str) -> Any:
        # gen_state 走进程内镜像：SSE 跟随循环每 0.15s 读它一次，直查库会把
        # 每条连接的轮询都压到 MySQL 上（DB 一抖动整个事件循环跟着冻结）。
        # 镜像即真相——生成状态本来就只在本进程有意义
        if name == "gen_state" and self._id in _GEN_STATE_MEM:
            return _GEN_STATE_MEM[self._id]
        with self._store._cursor() as cur:
            cur.execute("SELECT value FROM chat_slot WHERE session_id = %s AND name = %s",
                        (self._id, name))
            row = cur.fetchone()
        value = json.loads(row["value"]) if row else None
        if name == "gen_state":
            _GEN_STATE_MEM[self._id] = value   # 读回填：之后都走内存
        return value

    def set_slot(self, name: str, value: Any) -> None:
        if name == "gen_state":
            # 写穿透：先更镜像再落库。置位要立即生效（跟随循环马上要看）；
            # 清空早一两百毫秒无危害——尾部事件在 hub 日志里，不依赖这个槽位
            if value is None:
                _GEN_STATE_MEM.pop(self._id, None)
            else:
                _GEN_STATE_MEM[self._id] = value
        if value is None:
            with self._store._cursor() as cur:
                cur.execute("DELETE FROM chat_slot WHERE session_id = %s AND name = %s",
                            (self._id, name))
            return
        payload = json.dumps(value, ensure_ascii=False)
        now = _now()
        # 不用 VALUES()：MySQL 8.0.20 起已弃用，显式再传一遍参数最稳
        with self._store._cursor() as cur:
            cur.execute(
                "INSERT INTO chat_slot (session_id, name, value, updated_at) VALUES (%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE value = %s, updated_at = %s",
                (self._id, name, payload, now, payload, now))


class SessionStore:
    """会话库入口。首次使用才建表：导入模块不连库，测试与工具脚本不会被拖住。"""

    def __init__(self, database: str | None = None):
        self._database = database or config.CHAT_DB_NAME
        self._ready = False
        self._lock = threading.Lock()

    # ---- 连接 ----

    def _connect(self, database: str | None = None):
        return pymysql.connect(
            host=config.CHAT_DB_HOST, port=config.CHAT_DB_PORT,
            user=config.CHAT_DB_USER, password=config.CHAT_DB_PASSWORD,
            database=database if database is not None else self._database,
            charset="utf8mb4", autocommit=True, connect_timeout=5,
            cursorclass=DictCursor,
        )

    def _bootstrap(self) -> None:
        connection = self._connect()
        try:
            with connection.cursor() as cur:
                for statement in _SCHEMA:
                    cur.execute(statement)
        finally:
            connection.close()

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        if not self._ready:
            with self._lock:
                if not self._ready:
                    self._bootstrap()
                    self._ready = True
        connection = self._connect()
        try:
            with connection.cursor() as cur:
                yield cur
        finally:
            connection.close()

    # ---- 会话 ----

    def create(self, user_id: int, title: str = "新会话") -> Session:
        session_id = new_session_id()
        now = _now()
        # 首条消息可能带换行/多空格：压成一行再截断，否则侧栏标题会顶出两行
        clean = " ".join(str(title or "").split())[:20] or "新会话"
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO chat_session (id, user_id, title, mode, created_at, last_active_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (session_id, user_id, clean, "BUSINESS", now, now))
        return Session(self, session_id)

    def open(self, session_id: str, user_id: int) -> Session:
        """打开会话；不存在或不属于该用户都抛 SessionNotFound（404 语义）。"""
        if not SESSION_ID_RE.match(session_id or ""):
            raise SessionNotFound()
        with self._cursor() as cur:
            cur.execute("SELECT user_id FROM chat_session WHERE id = %s", (session_id,))
            row = cur.fetchone()
        if row is None or str(row["user_id"]) != str(user_id):
            raise SessionNotFound()
        return Session(self, session_id)

    def list_for(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """侧栏列表：按最近活跃倒序，最多 limit 条（设计 4 节）。

        20 条在真实使用里一两天就翻不到旧会话了；侧栏本身可滚动，
        50 条的内存与查询成本可以忽略。"""
        with self._cursor() as cur:
            cur.execute(
                "SELECT id, title, last_active_at FROM chat_session "
                "WHERE user_id = %s ORDER BY last_active_at DESC, id DESC LIMIT %s",
                (user_id, int(limit)))
            rows = cur.fetchall()
        return [{"sessionId": row["id"], "title": row["title"],
                 "lastActiveAt": _iso(row["last_active_at"])} for row in rows]

    def delete(self, session_id: str, user_id: int) -> None:
        """删除会话：消息与槽位靠外键级联删除，不留副本。"""
        self.open(session_id, user_id)
        _GEN_STATE_MEM.pop(session_id, None)   # 镜像与库一起清，别给已删会话留状态
        with self._cursor() as cur:
            cur.execute("DELETE FROM chat_session WHERE id = %s AND user_id = %s",
                        (session_id, user_id))
