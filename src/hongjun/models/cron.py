"""
吏部 · Cron 数据模型
===================

CronJob 和 CronSchedule Pydantic 模型。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class CronJobStatus(str, Enum):
    """定时任务状态"""
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"


class CronJob(BaseModel):
    """
    定时任务模型。

    标准化 cron 任务的定义和状态追踪。
    """
    model_config = ConfigDict(use_enum_values=True)

    # ── 标识 ─────────────────────────────────────────────────────────
    id: str
    name: str = ""

    # ── 调度 ─────────────────────────────────────────────────────────
    schedule: str = ""        # cron 表达式或 human-readable（e.g. "15m", "9:00")
    enabled: bool = True

    # ── 任务内容 ─────────────────────────────────────────────────────
    task_message: str = ""     # 触发时发送的消息
    task_type: str = "orchestrator"  # orchestrator | llm | skill

    # ── 状态 ─────────────────────────────────────────────────────────
    status: CronJobStatus = CronJobStatus.ACTIVE
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    run_count: int = 0
    fail_count: int = 0

    # ── 上次运行结果 ─────────────────────────────────────────────────
    last_result: Optional[str] = None
    last_error: Optional[str] = None

    # ── 元数据 ───────────────────────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow)
    description: str = ""      # 任务描述（供人类阅读）

    def mark_run(self, result: Optional[str] = None, error: Optional[str] = None) -> None:
        """标记一次运行"""
        self.last_run_at = datetime.utcnow()
        self.run_count += 1
        if error:
            self.fail_count += 1
            self.last_error = error
            self.status = CronJobStatus.FAILED
        else:
            self.last_result = result
            if self.status == CronJobStatus.FAILED:
                self.status = CronJobStatus.ACTIVE

    def pause(self) -> None:
        self.enabled = False
        self.status = CronJobStatus.PAUSED

    def resume(self) -> None:
        self.enabled = True
        self.status = CronJobStatus.ACTIVE
