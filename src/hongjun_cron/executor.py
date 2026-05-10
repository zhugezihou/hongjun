"""
鸿钧 Cron · 执行器
==================

负责实际执行定时任务：
  - Orchestrator: 直接在进程内调用鸿钧编排器
  - Webhook: 发送 HTTP POST 请求

执行器在独立线程池中运行，不阻塞主调度循环。
"""

from hongjun.logging_config import get_logger
import subprocess
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime, timezone
from typing import Optional

from .models import CronJob, CronTargetType, RunHistory
from .db import CronDB

logger = get_logger("hongjun.cron.executor")


# ============================================================
# Orchestrator 执行器（直接调用鸿钧编排器）
# ============================================================


class OrchestratorExecutor:
    """
    直接在进程内调用鸿钧编排器执行任务。

    不走 HTTP，不依赖 openclaw，直接调用 orchestrator.process_request()。
    """

    def __init__(self, timeout: int = 300):
        self.timeout = timeout

    def execute(self, job: CronJob) -> tuple[int, str]:
        """
        同步执行：直接调用鸿钧编排器。
        返回 (exit_code, output)
        """
        from hongjun.gateway.server import HongjunGateway

        message = job.target_message
        logger.info("direct_execute_start", job_id=job.id, message=message[:60])

        try:
            # 直接调用鸿钧内部编排器（同步）
            result = HongjunGateway._call_orchestrator_impl(
                message=message,
                platform=getattr(job, "target_platform", "feishu"),
            )
            logger.info("direct_job_ok", job_id=job.id, len=len(result))
            return 0, result[:500]
        except Exception as e:
            logger.error("direct_job_error", job_id=job.id, error=str(e), exc_info=True)
            return 1, str(e)


# ============================================================
# Webhook 执行器
# ============================================================

class WebhookExecutor:
    """
    通过 curl 发送 HTTP POST 请求到指定 URL。
    """

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    def execute(self, job: CronJob) -> tuple[int, str]:
        url = job.target_id.strip()
        data = job.target_message

        # 构造 curl 命令
        cmd = [
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "-X", "POST",
            "-H", "Content-Type: application/json",
            "-d", data,
            url,
        ]

        logger.info("webhook_post", url=url)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            http_code = result.stdout.strip()
            logger.info("webhook_success", url=url, http_code=http_code)
            return 0 if http_code.startswith("2") else int(http_code or 0), f"HTTP {http_code}"
        except subprocess.TimeoutExpired:
            return -1, f"Webhook timeout after {self.timeout}s"
        except Exception as e:
            logger.error("webhook_error", url=url, error=str(e), exc_info=True)
            return 1, str(e)


# ============================================================
# Cron 执行器
# ============================================================

class CronExecutor:
    """
    统一的 Cron 任务执行器。

    - 维护一个线程池（max_workers=4）用于并发执行任务
    - 每个任务创建一条 RunHistory 记录
    - 支持重试（根据 job.max_retries）
    """

    def __init__(self, db: Optional[CronDB] = None, max_workers: int = 4):
        self.db = db or CronDB.get_instance()
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cron_exec_")
        self.orchestrator = OrchestratorExecutor()
        self.webhook = WebhookExecutor()
        self._futures: dict[str, Future] = {}

    def submit(self, job: CronJob) -> Future:
        """
        将任务提交到执行线程池。
        返回 Future。
        """
        logger.info(f"[Executor] Submitting job {job.id} ({job.name})")
        fut = self.pool.submit(self._run_job_sync, job)
        self._futures[job.id] = fut
        fut.add_done_callback(lambda f: self._futures.pop(job.id, None))
        return fut

    def _run_job_sync(self, job: CronJob) -> RunHistory:
        """
        同步执行入口（在线程池线程中运行）。
        """
        started_at = datetime.now(timezone.utc).isoformat()
        run = RunHistory(job_id=job.id, started_at=started_at)

        try:
            if job.target_type == CronTargetType.ORCHESTRATOR:
                # 直接调用鸿钧编排器（同步）
                exit_code, output = self.orchestrator.execute(job)
            elif job.target_type == CronTargetType.WEBHOOK:
                exit_code, output = self.webhook.execute(job)
            else:
                exit_code, output = 1, f"Unknown target type: {job.target_type}"

        except Exception as e:
            logger.exception(f"[Executor] Job {job.id} exception")
            exit_code = 1
            output = str(e)

        run.exit_code = exit_code
        run.finished_at = datetime.now(timezone.utc).isoformat()
        run.status = "success" if exit_code == 0 else "failed"

        # 持久化执行结果
        self.db.insert_run(run)

        # 更新任务统计
        new_count = job.run_count + 1
        new_status = job.status.value
        self.db.update_job_last_run(job.id, started_at, new_count, new_status)

        logger.info(f"[Executor] Job {job.id} → {run.status} (exit={exit_code})")
        return run

    def shutdown(self, wait: bool = True):
        self.pool.shutdown(wait=wait)
