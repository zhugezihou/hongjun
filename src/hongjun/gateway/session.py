"""
鸿钧 · Session 状态机

状态流转：
  NEW → ACTIVE → IDLE → COMPRESSING → DONE
            ↑         │
            └─────────┘ (新消息进入)

状态说明：
- NEW:        会话刚创建，等待首条消息
- ACTIVE:     正在处理请求
- IDLE:       等待新消息（无活跃请求）
- COMPRESSING: 正在进行 Context Compaction
- DONE:       会话已结束，不再接收消息
"""

import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from .db import HongjunDB


class SessionState(str, Enum):
    NEW = "NEW"
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    COMPRESSING = "COMPRESSING"
    DONE = "DONE"


# 会话 idle 超时时间（秒），超时后状态从 ACTIVE → IDLE
IDLE_TIMEOUT = 300  # 5 分钟

# Context Compaction 阈值（消息条数），超过后触发压缩
COMPACTION_THRESHOLD = 50

# 压缩后保留的消息条数
COMPACTION_KEEP = 20


@dataclass
class Session:
    id: str
    platform: str = "local"
    platform_chat_id: Optional[str] = None
    state: SessionState = SessionState.NEW
    model: str = "MiniMax-M2.7"
    created_at: str = ""
    updated_at: str = ""
    last_active_at: str = ""
    message_count: int = 0
    metadata: dict = field(default_factory=dict)

    # 内存中的消息列表（从 DB 加载）
    _messages: list[dict] = field(default_factory=list, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _db: HongjunDB = field(default=None, repr=False)

    @classmethod
    def from_db_row(cls, row: dict, db: HongjunDB) -> "Session":
        meta = {}
        try:
            import json
            meta = json.loads(row.get("metadata", "{}"))
        except Exception:
            pass
        return cls(
            id=row["id"],
            platform=row.get("platform", "local"),
            platform_chat_id=row.get("platform_chat_id"),
            state=SessionState(row.get("state", "NEW")),
            model=row.get("model", "MiniMax-M2.7"),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
            last_active_at=row.get("last_active_at", ""),
            message_count=row.get("message_count", 0),
            metadata=meta,
            _db=db,
        )

    @classmethod
    def create(cls, db: HongjunDB, **kwargs) -> "Session":
        row = db.create_session(**kwargs)
        return cls.from_db_row(row, db)

    def load_messages(self):
        """从 DB 加载消息到内存"""
        with self._lock:
            self._messages = self._db.get_session_messages(self.id)
            self.message_count = len(self._messages)

    def add_message(self, role: str, content: str, **extra) -> dict:
        """添加消息并持久化到 DB"""
        with self._lock:
            msg = self._db.add_message(self.id, role, content, **extra)
            self._messages.append(msg)
            self.message_count += 1
            self._db.touch_session(self.id)
            self.updated_at = datetime.utcnow().isoformat()
            self.last_active_at = self.updated_at
            return msg

    def get_messages(self, limit: Optional[int] = None) -> list[dict]:
        """获取消息列表"""
        with self._lock:
            if limit:
                return self._messages[-limit:]
            return list(self._messages)

    def set_state(self, new_state: SessionState):
        """更新状态并持久化"""
        with self._lock:
            self.state = new_state
            self._db.update_session(self.id, state=new_state.value)

    def should_compact(self) -> bool:
        """判断是否需要 Context Compaction"""
        return self.message_count > COMPACTION_THRESHOLD

    def compact(self) -> int:
        """
        Context Compaction：压缩消息，保留最近 COMPACTION_KEEP 条。
        返回被压缩掉的消息数量。
        """
        if len(self._messages) <= COMPACTION_KEEP:
            return 0

        self.set_state(SessionState.COMPRESSING)

        kept = self._messages[-COMPACTION_KEEP:]
        removed_count = len(self._messages) - len(kept)

        # 记录压缩事件到 DB
        summary_msg = {
            "role": "system",
            "content": (
                f"[Context Compacted] "
                f"Removed {removed_count} messages, kept last {COMPACTION_KEEP}."
            ),
        }

        with self._lock:
            self._messages = kept + [summary_msg]
            self._db.update_session(
                self.id,
                message_count=len(self._messages),
                state=SessionState.IDLE.value,
            )
            self.state = SessionState.IDLE

        return removed_count

    def is_idle(self) -> bool:
        return self.state == SessionState.IDLE

    def is_active(self) -> bool:
        return self.state == SessionState.ACTIVE

    def is_done(self) -> bool:
        return self.state == SessionState.DONE


class SessionManager:
    """
    内存中的 Session 管理器。
    负责创建/获取/销毁 Session，支持从 DB 恢复。
    """

    def __init__(self, db: Optional[HongjunDB] = None):
        self._db = db or HongjunDB()
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    # ── CRUD ──────────────────────────────────────────────────────

    def create_session(
        self,
        platform: str = "local",
        platform_chat_id: Optional[str] = None,
        model: str = "MiniMax-M2.7",
    ) -> Session:
        with self._lock:
            session = Session.create(
                self._db,
                platform=platform,
                platform_chat_id=platform_chat_id,
                model=model,
            )
            self._sessions[session.id] = session
            return session

    def get_session(self, session_id: str) -> Optional[Session]:
        with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]
            # 未在内存中，尝试从 DB 加载
            row = self._db.get_session(session_id)
            if not row:
                return None
            session = Session.from_db_row(row, self._db)
            session.load_messages()
            self._sessions[session_id] = session
            return session

    def get_or_create_session(
        self,
        session_id: Optional[str] = None,
        platform: str = "local",
        platform_chat_id: Optional[str] = None,
        model: str = "MiniMax-M2.7",
    ) -> Session:
        """获取已有会话或创建新会话

        优先用 session_id 精确查找；若未提供，则按 platform+platform_chat_id
        在内存缓存中查找已存在的 session，避免飞书等平台每条消息都创新 session。
        """
        if session_id:
            existing = self.get_session(session_id)
            if existing:
                return existing

        # 按 platform+platform_chat_id 查找已有 session（避免每条消息都创新 session）
        if platform and platform_chat_id:
            for sess in self._sessions.values():
                if sess.platform == platform and sess.platform_chat_id == platform_chat_id:
                    return sess

        return self.create_session(
            platform=platform,
            platform_chat_id=platform_chat_id,
            model=model,
        )

    def list_sessions(self, platform: Optional[str] = None) -> list[Session]:
        rows = self._db.list_sessions(platform=platform)
        return [Session.from_db_row(r, self._db) for r in rows]

    def destroy_session(self, session_id: str):
        """将会话标记为 DONE，但不删除 DB 数据"""
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.set_state(SessionState.DONE)
                del self._sessions[session_id]
