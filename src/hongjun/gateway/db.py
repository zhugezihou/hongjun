"""
鸿钧 · 数据库层

SQLite 会话存储（hongjun_sessions.db）：
- sessions 表：会话元数据
- messages 表：会话消息历史
"""

import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent.parent.parent / "db" / "hongjun_sessions.db"


def get_db_path() -> Path:
    db_dir = Path(__file__).parent.parent.parent.parent / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "hongjun_sessions.db"


class HongjunDB:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_db_path()
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    platform TEXT DEFAULT 'local',
                    platform_chat_id TEXT,
                    state TEXT DEFAULT 'NEW',
                    model TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    last_active_at TEXT,
                    message_count INTEGER DEFAULT 0,
                    max_concurrent_turns INTEGER DEFAULT 1,
                    metadata TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    role TEXT,
                    content TEXT,
                    timestamp TEXT,
                    tokens INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}',
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, timestamp);

                CREATE TABLE IF NOT EXISTS crons (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    schedule TEXT,
                    prompt TEXT,
                    last_run TEXT,
                    next_run TEXT,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT,
                    metadata TEXT DEFAULT '{}'
                );
            """)

    # ── Session CRUD ──────────────────────────────────────────────

    def create_session(
        self,
        platform: str = "local",
        platform_chat_id: Optional[str] = None,
        model: str = "MiniMax-M2.7",
    ) -> dict:
        session_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO sessions
                   (id, platform, platform_chat_id, state, model,
                    created_at, updated_at, last_active_at)
                   VALUES (?, ?, ?, 'NEW', ?, ?, ?, ?)""",
                (session_id, platform, platform_chat_id, model,
                 now, now, now),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_session(self, session_id: str, **fields):
        if not fields:
            return
        fields["updated_at"] = datetime.utcnow().isoformat()
        cols = list(fields.keys())
        set_clause = ", ".join(f"{c} = ?" for c in cols)
        vals = list(fields.values()) + [session_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE sessions SET {set_clause} WHERE id = ?", vals
            )

    def touch_session(self, session_id: str):
        """更新 last_active_at 和 updated_at"""
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET last_active_at = ?, updated_at = ?"
                " WHERE id = ?",
                (now, now, session_id),
            )

    def list_sessions(
        self, platform: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        query = "SELECT * FROM sessions"
        params = []
        if platform:
            query += " WHERE platform = ?"
            params.append(platform)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ── Message CRUD ──────────────────────────────────────────────

    def add_message(
        self,
        session_id: str,
        role: str,  # "user" | "assistant" | "system"
        content: str,
        tokens: int = 0,
        metadata: Optional[dict] = None,
    ) -> dict:
        msg_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        meta = json.dumps(metadata or {})
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO messages
                   (id, session_id, role, content, timestamp, tokens, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (msg_id, session_id, role, content, now, tokens, meta),
            )
            conn.execute(
                """UPDATE sessions SET message_count = message_count + 1,
                   updated_at = ? WHERE id = ?""",
                (now, session_id),
            )
        return {
            "id": msg_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "timestamp": now,
            "tokens": tokens,
        }

    def get_session_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
    ) -> list[dict]:
        query = "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp"
        params: list = [session_id]
        if limit:
            query += " DESC LIMIT ?"
            params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        msgs = [dict(r) for r in rows]
        return list(reversed(msgs)) if limit else msgs

    def get_session_message_count(self, session_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT message_count FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return dict(row)["message_count"] if row else 0

    # ── Cron CRUD ─────────────────────────────────────────────────

    def create_cron(
        self, name: str, schedule: str, prompt: str, next_run: str
    ) -> dict:
        cron_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO crons
                   (id, name, schedule, prompt, next_run, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (cron_id, name, schedule, prompt, next_run, now),
            )
        return self.get_cron(cron_id)

    def get_cron(self, cron_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM crons WHERE id = ?", (cron_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_crons(self, enabled_only: bool = True) -> list[dict]:
        query = "SELECT * FROM crons"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY next_run"
        with self._conn() as conn:
            rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]

    def update_cron(self, cron_id: str, **fields):
        if not fields:
            return
        cols = list(fields.keys())
        set_clause = ", ".join(f"{c} = ?" for c in cols)
        vals = list(fields.values()) + [cron_id]
        with self._conn() as conn:
            conn.execute(f"UPDATE crons SET {set_clause} WHERE id = ?", vals)
