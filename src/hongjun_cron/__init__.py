"""
鸿钧 · Cron 调度系统
====================

设计原则：
  1. 持久化：所有定时任务存 SQLite，进程重启不丢失
  2. 精确调度：支持 cron 表达式、一次性、间隔重复
  3. 执行可靠：Orchestrator 直接调用 / HTTP 回调，并发控制
  4. 隔离执行：cron 任务在独立线程/进程中执行，不阻塞主 Gateway
  5. 历史可查：每次执行记录结果

调度类型：
  - cron:       标准 5 段 cron 表达式（分 时 日 月 周）
  - interval:   间隔重复（每 N 分钟/小时/天）
  - once:       一次性（指定时间，执行后自动禁用）

目标类型：
  - orchestrator: 直接调用鸿钧编排器
  - webhook:       HTTP POST 请求

优先级：
  - HIGH:   紧急任务，并发优先
  - NORMAL: 普通定时任务
  - LOW:    后台维护任务
"""

__version__ = "1.0.0"

from .scheduler import CronScheduler
from .manager import CronManager
from .models import CronJob, CronJobStatus, CronTargetType, RunHistory

__all__ = [
    "CronScheduler",
    "CronManager",
    "CronJob",
    "CronJobStatus",
    "CronTargetType",
    "RunHistory",
]
