"""
鸿钧 Cron · 调度器
==================

核心调度循环：
  1. 定期扫描数据库，找出所有 next_run_at <= now 的任务
  2. 将任务交给 Executor 并发执行
  3. 任务执行后重新计算下次执行时间并更新数据库
  4. 对于一次性任务，执行后自动标记为 completed

调度器本身是单线程的，通过线程池实现真正的并发执行。
"""

import asyncio
from hongjun.logging_config import get_logger
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from .db import CronDB
from .executor import CronExecutor
from .models import CronJob, CronScheduleType, CronJobStatus

logger = get_logger("hongjun.cron.scheduler")


class CronScheduler:
    """
    鸿钧 Cron 调度器。

    使用方式：
        scheduler = CronScheduler()
        scheduler.start()   # 后台线程启动
        scheduler.stop()    # 优雅停止
    """

    # 扫描间隔（秒）
    TICK_INTERVAL = 15

    def __init__(
        self,
        db: Optional[CronDB] = None,
        executor: Optional[CronExecutor] = None,
        tick_interval: int = TICK_INTERVAL,
    ):
        self.db = db or CronDB.get_instance()
        self.executor = executor or CronExecutor(db=self.db)
        self.tick_interval = tick_interval

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 默认不暂停
        self._running = False

        # 统计
        self._tick_count = 0
        self._jobs_run = 0

    # ============================================================
    # 生命周期
    # ============================================================

    def start(self):
        """启动调度器（后台线程）。"""
        if self._running:
            logger.warning("scheduler_already_running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="cron_scheduler", daemon=True)
        self._thread.start()
        self._running = True
        logger.info("scheduler_started")

    def stop(self, timeout: float = 10.0):
        """优雅停止调度器。"""
        if not self._running:
            return
        logger.info("scheduler_stopping")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._running = False
        logger.info("scheduler_stopped")

    def pause(self):
        self._pause_event.clear()
        logger.info("scheduler_paused")

    def resume(self):
        self._pause_event.set()
        logger.info("scheduler_resumed")

    @property
    def is_running(self) -> bool:
        return self._running

    # ============================================================
    # 调度主循环
    # ============================================================

    def _run_loop(self):
        """调度主循环（在独立线程中运行）。"""
        while not self._stop_event.is_set():
            # 暂停支持
            self._pause_event.wait(timeout=self.TICK_INTERVAL)

            if self._stop_event.is_set():
                break

            self._tick_count += 1

            try:
                self._tick()
            except Exception:
                logger.exception("[Scheduler] Tick error")

    def _tick(self):
        """
        一次调度扫描。
        1. 查找所有到期任务
        2. 提交执行
        3. 更新下次执行时间
        """
        due = self.db.get_due_jobs()
        if not due:
            return

        logger.debug("scheduler_jobs_due", count=len(due))
        for job in due:
            self._dispatch(job)

    def _dispatch(self, job: CronJob):
        """
        派发任务到执行器。
        执行后重新计算 next_run_at。
        """
        self._jobs_run += 1
        job.run_count += 1

        # 标记为 running（状态暂时不变，等执行器回调）
        logger.info(
            "scheduler_dispatching",
            job_name=job.name,
            schedule_type=job.schedule_type.value,
            target_id=job.target_id,
        )

        # 提交执行（异步，不阻塞调度循环）
        self.executor.submit(job)

        # 重新计算下次执行时间
        if job.schedule_type == CronScheduleType.ONCE:
            # 一次性任务：执行后标记完成
            job.status = CronJobStatus.COMPLETED
            job.next_run_at = None
        else:
            # 周期任务：计算下次
            next_dt = job.calc_next_run()
            if next_dt:
                job.next_run_at = next_dt.replace(tzinfo=timezone.utc).isoformat()
            else:
                # 无法计算下次，暂停
                job.status = CronJobStatus.PAUSED
                job.next_run_at = None

        # 持久化更新
        self.db.upsert_job(job)

    # ============================================================
    # 手动触发（立即执行，跳过调度）
    # ============================================================

    def run_now(self, job_id: str) -> bool:
        """
        手动立即触发一个任务。
        返回是否成功提交。
        """
        job = self.db.get_job(job_id)
        if not job:
            logger.warning("scheduler_job_not_found", job_id=job_id)
            return False
        self._dispatch(job)
        return True

    # ============================================================
    # 统计
    # ============================================================

    def stats(self) -> dict:
        return {
            "running": self._running,
            "tick_count": self._tick_count,
            "jobs_dispatched": self._jobs_run,
            "executor_alive": not self.executor.pool._shutdown,
        }
