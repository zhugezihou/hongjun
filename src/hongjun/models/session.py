"""
工部 · 会话数据模型
==================

Session 和 SessionState Pydantic 模型。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict

from .message import Message, MessageRole


class SessionState(str, Enum):
    """会话状态"""
    IDLE = "idle"
    ACTIVE = "active"
    DONE = "done"
    ERROR = "error"


class Session(BaseModel):
    """
    会话模型。

    标准化 Session 对象，统一管理：
    - 元信息（id, platform, model）
    - 消息历史（messages）
    - 状态机（state）
    - 压缩追踪（message_count, last_compact_at）

    兼容旧版 dict-based 访问习惯（通过属性访问）。
    """
    model_config = ConfigDict(
        extra="allow",
        use_enum_values=True,
    )

    # ── 标识 ─────────────────────────────────────────────────────────
    id: str
    platform: str = "feishu"
    platform_chat_id: Optional[str] = None

    # ── LLM 配置 ─────────────────────────────────────────────────────
    model: str = "MiniMax-M2.7"
    temperature: float = 0.3

    # ── 状态 ─────────────────────────────────────────────────────────
    state: SessionState = SessionState.IDLE

    # ── 消息历史 ─────────────────────────────────────────────────────
    messages: list[Message] = Field(default_factory=list)

    # ── 时间戳 ───────────────────────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_compact_at: Optional[datetime] = None

    # ── 消息计数 ─────────────────────────────────────────────────────
    message_count: int = 0

    # 压缩阈值（类属性，不参与序列化）
    _compact_threshold: int = 20

    # ── 质量追踪 ─────────────────────────────────────────────────────
    total_latency_s: float = 0.0
    request_count: int = 0

    # ── 辅助属性 ─────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self.state == SessionState.ACTIVE

    @property
    def is_done(self) -> bool:
        return self.state == SessionState.DONE

    def get_messages(self) -> list[Message]:
        """获取消息列表（兼容旧 API）"""
        return self.messages

    def get_openai_messages(self) -> list[dict]:
        """获取 OpenAI 格式的消息列表"""
        return [m.to_openai_dict() for m in self.messages]

    def get_brief_history(self, limit: int = 5) -> list[dict]:
        """获取最近 N 条消息的简短摘要"""
        recent = self.messages[-limit:] if len(self.messages) > limit else self.messages
        return [m.to_brief_dict() for m in recent]

    # ── 状态操作 ─────────────────────────────────────────────────────

    def set_state(self, state: SessionState) -> None:
        self.state = state
        self.updated_at = datetime.utcnow()

    def should_compact(self) -> bool:
        """判断是否需要压缩（消息数超过阈值）"""
        return self.message_count >= self._compact_threshold

    # ── 消息操作 ─────────────────────────────────────────────────────

    def add_message(
        self,
        role: str,
        content: str,
        **kwargs,
    ) -> Message:
        """添加一条消息，自动更新时间戳和计数"""
        if role not in [r.value for r in MessageRole]:
            role = MessageRole.USER.value if role == "user" else MessageRole.ASSISTANT.value

        msg = Message(
            role=role,  # type: ignore
            content=content,
            **kwargs,
        )
        self.messages.append(msg)
        self.message_count += 1
        self.updated_at = datetime.utcnow()
        return msg

    def compact(self) -> int:
        """
        压缩会话历史。

        策略：保留前2条（system + 首次 user），中间摘要，最后2条。
        返回压缩掉的消息数量。
        """
        if len(self.messages) <= 4:
            return 0

        kept = []
        removed = 0

        # 保留开头
        kept.append(self.messages[0])  # system

        # 找第一条 user 消息
        first_user_idx = 0
        for i, m in enumerate(self.messages):
            if m.role == MessageRole.USER.value:
                first_user_idx = i
                break
        kept.append(self.messages[first_user_idx])

        # 中间摘要
        middle = self.messages[first_user_idx + 1 : -2]
        if middle:
            summary_text = f"【{len(middle)} 条消息已压缩】"
            kept.append(Message(
                role=MessageRole.SYSTEM.value,
                content=summary_text,
            ))
            removed = len(middle)

        # 保留结尾2条
        kept.extend(self.messages[-2:])

        self.messages = kept
        self.last_compact_at = datetime.utcnow()
        return removed

    # ── 序列化兼容 ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """转换为 dict（兼容旧 API）"""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        """从 dict 恢复（兼容旧 API）"""
        # 处理 messages 字段：dict → Message
        if "messages" in d and d["messages"]:
            d = dict(d)
            messages = []
            for m in d["messages"]:
                if isinstance(m, dict):
                    messages.append(Message(**m))
                else:
                    messages.append(m)
            d["messages"] = messages
        return cls(**d)
