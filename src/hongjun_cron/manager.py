"""
鸿钧 Cron · 管理器
==================

提供完整的 CronJob CRUD + 调度器生命周期管理。

这是对外暴露的主要 API。
"""

from datetime import datetime, timezone
from typing import Optional, List

from .models import (
    CronJob,
    CronJobStatus,
    CronTargetType,
    CronScheduleType,
    RunHistory,
)
from .db import CronDB
from .scheduler import CronScheduler
from .executor import CronExecutor
from hongjun.logging_config import get_logger

logger = get_logger("hongjun.cron.manager")


class CronManager:
    """
    鸿钧 Cron 管理器。

    使用方式：

        # 启动调度器（通常在 Hongjun Gateway 启动时调用）
        manager = CronManager()
        manager.start()

        # 管理任务
        job = manager.create_job(
            name="六部健康检查",
            schedule_type="interval",
            schedule_value="30m",
            target_type="orchestrator",
            target_id="main",
            target_message="六部健康检查",
        )
        manager.list_jobs()
        manager.disable_job(job.id)
        manager.delete_job(job.id)

        # 停止
        manager.stop()
    """

    def __init__(
        self,
        db: Optional[CronDB] = None,
        scheduler: Optional[CronScheduler] = None,
    ):
        self.db = db or CronDB.get_instance()
        self.scheduler = scheduler or CronScheduler(db=self.db)
        self.executor = self.scheduler.executor

    # ============================================================
    # 生命周期
    # ============================================================

    def start(self):
        """启动 Cron 调度器。"""
        self.scheduler.start()
        logger.info("cronmanager_started")

    def stop(self):
        """停止 Cron 调度器。"""
        self.scheduler.stop()
        self.executor.shutdown()
        logger.info("cronmanager_stopped")

    def restart(self):
        """重启调度器。"""
        self.scheduler.stop()
        self.start()

    # ============================================================
    # 任务 CRUD
    # ============================================================

    def create_job(
        self,
        name: str,
        target_type: str,
        target_id: str,
        target_message: str,
        schedule_type: str = "cron",
        schedule_value: str = "*/5 * * * *",
        description: str = "",
        priority: str = "normal",
        max_retries: int = 3,
        timeout_seconds: int = 300,
        enabled: bool = True,
        creator: str = "cli",
        metadata: Optional[dict] = None,
    ) -> CronJob:
        """
        创建新定时任务。
        自动计算首次 next_run_at。
        """
        job = CronJob(
            name=name,
            target_type=CronTargetType(target_type),
            target_id=target_id,
            target_message=target_message,
            schedule_type=CronScheduleType(schedule_type),
            schedule_value=schedule_value,
            description=description,
            priority=priority,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            enabled=enabled,
            creator=creator,
            metadata=metadata or {},
        )

        # 计算下次执行时间
        next_dt = job.calc_next_run()
        if next_dt:
            job.next_run_at = next_dt.replace(tzinfo=timezone.utc).isoformat()
            job.status = CronJobStatus.ACTIVE if enabled else CronJobStatus.PAUSED

        self.db.upsert_job(job)
        logger.info("cronmanager_job_created", job_id=job.id, name=name)
        return job

    def get_job(self, job_id: str) -> Optional[CronJob]:
        return self.db.get_job(job_id)

    def list_jobs(
        self,
        status: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> List[CronJob]:
        """
        列出任务。
        status: "active" | "paused" | "completed" | "failed"
        """
        s = CronJobStatus(status) if status else None
        return self.db.list_jobs(status=s, enabled=enabled)

    def update_job(
        self,
        job_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        schedule_type: Optional[str] = None,
        schedule_value: Optional[str] = None,
        target_message: Optional[str] = None,
        priority: Optional[str] = None,
        max_retries: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> Optional[CronJob]:
        """
        更新任务（部分更新，只更新提供的字段）。
        """
        job = self.db.get_job(job_id)
        if not job:
            return None

        if name is not None:
            job.name = name
        if description is not None:
            job.description = description
        if schedule_type is not None:
            job.schedule_type = CronScheduleType(schedule_type)
        if schedule_value is not None:
            job.schedule_value = schedule_value
        if target_message is not None:
            job.target_message = target_message
        if priority is not None:
            job.priority = priority
        if max_retries is not None:
            job.max_retries = max_retries
        if timeout_seconds is not None:
            job.timeout_seconds = timeout_seconds
        if enabled is not None:
            job.enabled = enabled
            job.status = CronJobStatus.ACTIVE if enabled else CronJobStatus.PAUSED

        # 重新计算 next_run_at（如果调度相关字段变了）
        if schedule_type or schedule_value:
            next_dt = job.calc_next_run()
            if next_dt:
                job.next_run_at = next_dt.replace(tzinfo=timezone.utc).isoformat()

        self.db.upsert_job(job)
        logger.info("cronmanager_job_updated", job_id=job_id)
        return job

    def delete_job(self, job_id: str) -> bool:
        """删除任务。"""
        ok = self.db.delete_job(job_id)
        if ok:
            logger.info("cronmanager_job_deleted", job_id=job_id)
        return ok

    def enable_job(self, job_id: str) -> Optional[CronJob]:
        """启用任务。"""
        job = self.db.get_job(job_id)
        if not job:
            return None
        job.enabled = True
        job.status = CronJobStatus.ACTIVE
        next_dt = job.calc_next_run()
        if next_dt:
            job.next_run_at = next_dt.replace(tzinfo=timezone.utc).isoformat()
        self.db.upsert_job(job)
        return job

    def disable_job(self, job_id: str) -> Optional[CronJob]:
        """禁用任务（暂停）。"""
        job = self.db.get_job(job_id)
        if not job:
            return None
        job.enabled = False
        job.status = CronJobStatus.PAUSED
        self.db.upsert_job(job)
        return job

    def trigger_job(self, job_id: str) -> bool:
        """
        手动立即触发任务（跳过调度立即执行）。
        """
        return self.scheduler.run_now(job_id)

    # ============================================================
    # 执行历史
    # ============================================================

    def get_history(self, job_id: str, limit: int = 20) -> List[RunHistory]:
        """获取任务执行历史。"""
        return self.db.list_runs(job_id, limit=limit)

    def get_last_run(self, job_id: str) -> Optional[RunHistory]:
        """获取最近一次执行记录。"""
        return self.db.get_last_run(job_id)

    # ============================================================
    # 统计
    # ============================================================

    def status(self) -> dict:
        """返回完整状态信息。"""
        all_jobs = self.db.list_jobs()
        active = self.db.list_jobs(status=CronJobStatus.ACTIVE)
        return {
            "scheduler": self.scheduler.stats(),
            "jobs": {
                "total": len(all_jobs),
                "active": len(active),
                "paused": sum(1 for j in all_jobs if j.status == CronJobStatus.PAUSED),
                "completed": sum(1 for j in all_jobs if j.status == CronJobStatus.COMPLETED),
            },
            "executor": {
                "max_workers": self.executor.pool._max_workers,
            },
        }
