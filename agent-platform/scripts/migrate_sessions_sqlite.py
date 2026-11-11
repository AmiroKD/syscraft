"""把旧的 SQLite 会话文件一次性导入 chat 库（迁移脚本，可重复执行）。

背景：会话存储从「每会话一个 SQLite 文件」改成 MySQL 的 chat 库。
历史会话不该因为换存储就消失——这个脚本按文件逐个搬，搬完把目录整体归档，
不删除（保留回退余地）。

去重：同一个 session_id 已存在就跳过，所以重复跑不会产生重复会话。
用法：
    set -a; source ~/.adaptive/db.env; set +a
    PYTHONPATH=src .venv/bin/python scripts/migrate_sessions_sqlite.py
    # 只演练不写入：--dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from syscraft import config  # noqa: E402
from syscraft.conversation.store import SessionStore  # noqa: E402


def _read(path: Path) -> dict | None:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        with connection:
            meta = {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM meta")}
            messages = [dict(row) for row in connection.execute(
                "SELECT seq, role, type, content, tool_call, created_at FROM messages ORDER BY seq")]
            slots = [dict(row) for row in connection.execute("SELECT name, value FROM slots")]
        connection.close()
    except sqlite3.DatabaseError as error:
        print(f"  跳过 {path.name}：{error}")
        return None
    if not meta.get("user_id"):
        print(f"  跳过 {path.name}：没有 user_id")
        return None
    return {"meta": meta, "messages": messages, "slots": slots}


def _dt(value: str | None) -> datetime:
    try:
        return datetime.fromisoformat(value) if value else datetime.now().replace(microsecond=0)
    except ValueError:
        return datetime.now().replace(microsecond=0)


def migrate(root: Path, dry_run: bool) -> tuple[int, int]:
    files = sorted(root.glob("*.db"))
    if not files:
        print(f"{root} 下没有 .db 文件")
        return 0, 0

    store = SessionStore()
    moved = skipped = 0
    for path in files:
        data = _read(path)
        if data is None:
            skipped += 1
            continue
        meta, messages, slots = data["meta"], data["messages"], data["slots"]
        session_id = path.stem
        if dry_run:
            print(f"  [dry] {session_id}：{len(messages)} 条消息、{len(slots)} 个槽位")
            moved += 1
            continue
        with store._cursor() as cur:
            cur.execute("SELECT 1 FROM chat_session WHERE id = %s", (session_id,))
            if cur.fetchone():
                print(f"  跳过 {session_id}：库里已存在")
                skipped += 1
                continue
            cur.execute(
                "INSERT INTO chat_session (id, user_id, title, mode, created_at, last_active_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (session_id, int(meta.get("user_id", 0)), meta.get("title", "")[:20],
                 meta.get("mode", "BUSINESS"),
                 _dt(meta.get("created_at")), _dt(meta.get("last_active_at"))))
            for message in messages:
                cur.execute(
                    "INSERT INTO chat_message (session_id, `role`, `type`, content, tool_call, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (session_id, message["role"], message["type"], message["content"],
                     message["tool_call"], _dt(message["created_at"])))
            for slot in slots:
                cur.execute(
                    "INSERT INTO chat_slot (session_id, name, value, updated_at) VALUES (%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE value = %s",
                    (session_id, slot["name"], slot["value"], datetime.now(), slot["value"]))
        print(f"  已导入 {session_id}：{len(messages)} 条消息、{len(slots)} 个槽位")
        moved += 1
    return moved, skipped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(config.PROJECT_ROOT / "workspace" / "sessions"))
    parser.add_argument("--archive", default=None, help="导入后把旧目录移到哪里（默认 workspace/_trash）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    print(f"会话库：{config.CHAT_DB_HOST}:{config.CHAT_DB_PORT}/{config.CHAT_DB_NAME}")
    print(f"旧文件：{root}")
    moved, skipped = migrate(root, args.dry_run)
    print(f"导入 {moved} 个，跳过 {skipped} 个")

    if args.dry_run or moved == 0 or not root.exists():
        return
    archive = Path(args.archive) if args.archive else config.PROJECT_ROOT / "workspace/_trash"
    target = archive / f"sessions-sqlite-{datetime.now():%Y%m%d-%H%M%S}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(root), str(target))
    print(f"旧目录已归档：{target}（确认无误后可手工删除）")


if __name__ == "__main__":
    main()
