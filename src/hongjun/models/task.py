"""
刑部 · 任务数据模型
==================

Task 和 TaskStatus Pydantic 模型。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    """任务类型"""
    ORCHESTRATOR = "orchestrator"   # 复杂任务走编排器
    LLM = "llm"                     # 简单问答走 LLM
    SKILL = "skill"                 # 工具/skill 调用
    CRON = "cron"                   # 定时任务


class Task(BaseModel):
    """
    任务模型。

    标准化一次请求的生命周期追踪。
    """
    model_config = ConfigDict(use_enum_values=True)

    # ── 标识 ─────────────────────────────────────────────────────────
    id: str
    type: TaskType = TaskType.LLM

    # ── 输入 ─────────────────────────────────────────────────────────
    message: str = ""
    platform: str = "feishu"
    platform_chat_id: Optional[str] = None

    # ── 输出 ─────────────────────────────────────────────────────────
    result: Optional[str] = None
    error: Optional[str] = None

    # ── 状态 ─────────────────────────────────────────────────────────
    status: TaskStatus = TaskStatus.PENDING

    # ── 质量 ─────────────────────────────────────────────────────────
    eval_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    blocked: bool = False

    # ── 性能 ─────────────────────────────────────────────────────────
    latency_s: Optional[float] = None
    tokens_used: Optional[int] = None

    # ── 时间 ─────────────────────────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def mark_running(self) -> None:
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.utcnow()

    def mark_done(self, result: str, **kwargs) -> None:
        self.status = TaskStatus.DONE
        self.result = result
        self.completed_at = datetime.utcnow()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def mark_failed(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.error = error
        self.completed_at = datetime.utcnow()
