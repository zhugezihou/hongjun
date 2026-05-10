"""
工部 · 消息数据模型
==================

所有内部消息的标准化 Pydantic 模型。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional, Literal

from pydantic import BaseModel, Field, ConfigDict


class MessageRole(str, Enum):
    """消息角色"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    FUNCTION = "function"


class Message(BaseModel):
    """
    标准化消息模型。

    用途：
    - Session.messages 里的每条消息
    - 工具调用的输入/输出
    - 跨组件传递的消息对象

    兼容 OpenAI messages 格式 + 鸿钧扩展字段。
    """
    model_config = ConfigDict(
        extra="allow",          # 允许额外字段（如 metadata）
        use_enum_values=True,   # 序列化时用 value 而非 enum 名
    )

    role: Literal["system", "user", "assistant", "tool", "function"]
    content: str = ""

    # ── 扩展字段 ─────────────────────────────────────────────────────
    name: Optional[str] = Field(default=None, description="消息发送者名称（如 tool 名）")
    tool_call_id: Optional[str] = Field(default=None, description="工具调用 ID（tool role 时必填）")
    tool_name: Optional[str] = Field(default=None, description="调用的工具名")

    # ── 元数据 ───────────────────────────────────────────────────────
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    model: Optional[str] = Field(default=None, description="生成此消息的模型名")
    tokens: Optional[int] = Field(default=None, description="此消息的 token 数（估算）")

    # ── 质量追踪 ────────────────────────────────────────────────────
    blocked: bool = Field(default=False, description="是否被安全审核拦截")
    eval_score: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="质量评分")

    def to_openai_dict(self) -> dict:
        """转换为 OpenAI messages API 格式"""
        d = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d

    def to_brief_dict(self) -> dict:
        """简短摘要（用于日志/压缩）"""
        content = self.content
        if len(content) > 80:
            content = content[:80] + "..."
        return {
            "role": self.role,
            "content": content,
            "timestamp": self.timestamp.isoformat(),
        }
