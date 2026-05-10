"""
鸿钧 Cron · 数据模型
"""

import json
import uuid
import croniter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Literal


# ============================================================
# 枚举
# ============================================================

class CronJobStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"   # 一次性任务执行完毕
    FAILED = "failed"


class CronTargetType(str, Enum):
    ORCHESTRATOR = "orchestrator"  # 直接调用鸿钧编排器
    WEBHOOK = "webhook"            # HTTP POST


class CronScheduleType(str, Enum):
    CRON = "cron"       # cron 表达式
    INTERVAL = "interval"  # 间隔重复
    ONCE = "once"       # 一次性


# ============================================================
# 主模型
# ============================================================

@dataclass
class CronJob:
    """
    定时任务定义。

    属性：
      id:               唯一 ID（uuid4）
      name:             任务名称（用户友好）
      description:      任务描述
      enabled:          是否启用
      status:           当前状态（active/paused/completed/failed）
      schedule_type:     调度类型（cron/interval/once）
      schedule_value:    调度值（如 "*/5 * * * *" 或 "30m" 或 "2026-05-06T10:00:00"）
      target_type:      执行目标类型（orchestrator/webhook）
      target_id:        目标 ID（webhook URL 或保留字段）
      target_message:   发送给 Agent 或 POST 到 webhook 的内容
      priority:         优先级（high/normal/low）
      max_retries:      失败最大重试次数
      timeout_seconds:  执行超时（秒），超时强制终止
      created_at:       创建时间（ISO）
      updated_at:       更新时间（ISO）
      last_run_at:      上次执行时间（ISO）
      next_run_at:      下次执行时间（ISO）
      run_count:        累计执行次数
      creator:          创建者（agent_id 或 "cli"）
      metadata:         扩展元数据（JSON）
    """

    name: str
    target_type: CronTargetType
    target_id: str
    target_message: str
    schedule_type: CronScheduleType = CronScheduleType.CRON
    schedule_value: str = "*/5 * * * *"
    description: str = ""
    enabled: bool = True
    status: CronJobStatus = CronJobStatus.ACTIVE
    priority: Literal["high", "normal", "low"] = "normal"
    max_retries: int = 3
    timeout_seconds: int = 300
    created_at: str = ""
    updated_at: str = ""
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    run_count: int = 0
    creator: str = "cli"
    metadata: dict = field(default_factory=dict)
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["target_type"] = self.target_type.value
        d["schedule_type"] = self.schedule_type.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CronJob":
        d = dict(d)
        d["status"] = CronJobStatus(d.get("status", "active"))
        target_type_val = d.get("target_type", "orchestrator")
        if target_type_val == "a2a":
            target_type_val = "orchestrator"  # 兼容旧数据
        d["target_type"] = CronTargetType(target_type_val)
        d["schedule_type"] = CronScheduleType(d.get("schedule_type", "cron"))
        # metadata
        if isinstance(d.get("metadata"), str):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except Exception:
                d["metadata"] = {}
        return cls(**d)

    def calc_next_run(self, from_time: Optional[datetime] = None) -> Optional[datetime]:
        """
        计算下次执行时间。
        返回 None 表示不会再执行（一次性任务已过或已禁用）。
        """
        if self.schedule_type == CronScheduleType.ONCE:
            try:
                dt = datetime.fromisoformat(self.schedule_value)
                return dt
            except Exception:
                return None

        elif self.schedule_type == CronScheduleType.CRON:
            try:
                now = from_time or datetime.now(timezone.utc)
                # croniter 需要 naive datetime
                naive = now.replace(tzinfo=None)
                cr = croniter.croniter(self.schedule_value, naive)
                return cr.get_next(datetime)
            except Exception:
                return None

        elif self.schedule_type == CronScheduleType.INTERVAL:
            return self._calc_interval_next(from_time)

        return None

    def _calc_interval_next(self, from_time=None) -> Optional[datetime]:
        """计算间隔型任务的下次执行时间。"""
        import datetime as dt_module

        val = self.schedule_value.lower().strip()  # 如 "30m", "2h", "1d"
        try:
            base = from_time or datetime.now(timezone.utc)
            if val.endswith("m"):
                minutes = int(val[:-1])
                return base + dt_module.timedelta(minutes=minutes)
            elif val.endswith("h"):
                hours = int(val[:-1])
                return base + dt_module.timedelta(hours=hours)
            elif val.endswith("d"):
                days = int(val[:-1])
                return base + dt_module.timedelta(days=days)
        except Exception:
            pass
        return None


@dataclass
class RunHistory:
    """
    单次执行记录。
    """
    id: str = ""
    job_id: str = ""
    started_at: str = ""
    finished_at: Optional[str] = None
    status: str = "success"  # success / failed / timeout / cancelled
    result: Optional[str] = None  # 返回信息或错误
    exit_code: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RunHistory":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
