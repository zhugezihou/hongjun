"""
鸿钧 Cron · 持久化层（SQLite）
"""

import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List
from contextlib import contextmanager

from .models import CronJob, CronJobStatus, RunHistory


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# 数据库路径
# ============================================================

def _get_db_path() -> Path:
    db_dir = Path(__file__).parent.parent.parent / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "hongjun_cron.db"


# ============================================================
# 连接管理
# ============================================================

class CronDB:
    _instance = None

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or _get_db_path()
        self._init_db()

    @classmethod
    def get_instance(cls, **kwargs) -> "CronDB":
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ============================================================
    # 初始化
    # ============================================================

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS cron_jobs (
                    id              TEXT PRIMARY KEY,
                    name            TEXT NOT NULL,
                    description     TEXT DEFAULT '',
                    enabled         INTEGER DEFAULT 1,
                    status          TEXT DEFAULT 'active',
                    schedule_type   TEXT DEFAULT 'cron',
                    schedule_value  TEXT DEFAULT '*/5 * * * *',
                    target_type     TEXT DEFAULT 'orchestrator',
                    target_id       TEXT NOT NULL DEFAULT '',
                    target_message  TEXT NOT NULL DEFAULT '',
                    priority        TEXT DEFAULT 'normal',
                    max_retries     INTEGER DEFAULT 3,
                    timeout_seconds INTEGER DEFAULT 300,
                    created_at      TEXT,
                    updated_at      TEXT,
                    last_run_at     TEXT,
                    next_run_at     TEXT,
                    run_count       INTEGER DEFAULT 0,
                    creator         TEXT DEFAULT 'cli',
                    metadata        TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS run_history (
                    id          TEXT PRIMARY KEY,
                    job_id      TEXT NOT NULL,
                    started_at  TEXT,
                    finished_at TEXT,
                    status      TEXT DEFAULT 'success',
                    result      TEXT,
                    exit_code   INTEGER,
                    FOREIGN KEY (job_id) REFERENCES cron_jobs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status ON cron_jobs(status);
                CREATE INDEX IF NOT EXISTS idx_jobs_next_run ON cron_jobs(next_run_at);
                CREATE INDEX IF NOT EXISTS idx_history_job ON run_history(job_id);
            """)

    # ============================================================
    # CronJob CRUD
    # ============================================================

    def upsert_job(self, job: CronJob) -> CronJob:
        """创建或更新任务。"""
        now = _utcnow()
        job.updated_at = now
        if not job.created_at:
            job.created_at = now

        with self._conn() as conn:
            conn.execute("""
                INSERT INTO cron_jobs (
                    id, name, description, enabled, status, schedule_type,
                    schedule_value, target_type, target_id, target_message,
                    priority, max_retries, timeout_seconds,
                    created_at, updated_at, last_run_at, next_run_at,
                    run_count, creator, metadata
                ) VALUES (
                    :id, :name, :description, :enabled, :status, :schedule_type,
                    :schedule_value, :target_type, :target_id, :target_message,
                    :priority, :max_retries, :timeout_seconds,
                    :created_at, :updated_at, :last_run_at, :next_run_at,
                    :run_count, :creator, :metadata
                ) ON CONFLICT(id) DO UPDATE SET
                    name            = excluded.name,
                    description     = excluded.description,
                    enabled         = excluded.enabled,
                    status          = excluded.status,
                    schedule_type   = excluded.schedule_type,
                    schedule_value  = excluded.schedule_value,
                    target_type     = excluded.target_type,
                    target_id       = excluded.target_id,
                    target_message  = excluded.target_message,
                    priority        = excluded.priority,
                    max_retries     = excluded.max_retries,
                    timeout_seconds = excluded.timeout_seconds,
                    updated_at       = excluded.updated_at,
                    last_run_at     = excluded.last_run_at,
                    next_run_at     = excluded.next_run_at,
                    run_count       = excluded.run_count,
                    metadata        = excluded.metadata
            """, {
                "id": job.id,
                "name": job.name,
                "description": job.description,
                "enabled": 1 if job.enabled else 0,
                "status": job.status.value,
                "schedule_type": job.schedule_type.value,
                "schedule_value": job.schedule_value,
                "target_type": job.target_type.value,
                "target_id": job.target_id,
                "target_message": job.target_message,
                "priority": job.priority,
                "max_retries": job.max_retries,
                "timeout_seconds": job.timeout_seconds,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "last_run_at": job.last_run_at,
                "next_run_at": job.next_run_at,
                "run_count": job.run_count,
                "creator": job.creator,
                "metadata": json.dumps(job.metadata),
            })
        return job

    def get_job(self, job_id: str) -> Optional[CronJob]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM cron_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row:
            return CronJob.from_dict(dict(row))
        return None

    def list_jobs(
        self,
        status: Optional[CronJobStatus] = None,
        enabled: Optional[bool] = None,
    ) -> List[CronJob]:
        query = "SELECT * FROM cron_jobs WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if enabled is not None:
            query += " AND enabled = ?"
            params.append(1 if enabled else 0)
        query += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [CronJob.from_dict(dict(r)) for r in rows]

    def delete_job(self, job_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM cron_jobs WHERE id = ?", (job_id,)
            )
            return cur.rowcount > 0

    def update_job_next_run(self, job_id: str, next_run_at: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE cron_jobs SET next_run_at = ? WHERE id = ?",
                (next_run_at, job_id)
            )

    def update_job_last_run(
        self, job_id: str, last_run_at: str, run_count: int, status: str
    ):
        with self._conn() as conn:
            conn.execute("""
                UPDATE cron_jobs
                SET last_run_at = ?, run_count = ?, status = ?
                WHERE id = ?
            """, (last_run_at, run_count, status, job_id))

    def get_due_jobs(self, before_dt: Optional[datetime] = None) -> List[CronJob]:
        """
        找出所有已到执行时间的任务。
        next_run_at <= now 且 enabled=1 且 status=active。
        """
        now = before_dt or datetime.now(timezone.utc)
        now_str = now.isoformat()
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM cron_jobs
                WHERE enabled = 1
                  AND status = 'active'
                  AND (next_run_at IS NULL OR next_run_at <= ?)
                ORDER BY
                    CASE priority
                        WHEN 'high'   THEN 0
                        WHEN 'normal' THEN 1
                        WHEN 'low'    THEN 2
                    END,
                    next_run_at ASC
            """, (now_str,)).fetchall()
        return [CronJob.from_dict(dict(r)) for r in rows]

    # ============================================================
    # RunHistory
    # ============================================================

    def insert_run(self, run: RunHistory) -> RunHistory:
        if not run.id:
            run.id = str(uuid.uuid4())[:8]
        if not run.started_at:
            run.started_at = _utcnow()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO run_history (id, job_id, started_at, finished_at, status, result, exit_code)
                VALUES (:id, :job_id, :started_at, :finished_at, :status, :result, :exit_code)
            """, run.to_dict())
        return run

    def list_runs(self, job_id: str, limit: int = 50) -> List[RunHistory]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM run_history
                WHERE job_id = ?
                ORDER BY started_at DESC
                LIMIT ?
            """, (job_id, limit)).fetchall()
        return [RunHistory.from_dict(dict(r)) for r in rows]

    def get_last_run(self, job_id: str) -> Optional[RunHistory]:
        runs = self.list_runs(job_id, limit=1)
        return runs[0] if runs else None
