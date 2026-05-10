"""
鸿钧 · 任务状态持久化
=====================

任务中断恢复：每个任务的执行状态持久化到磁盘，中断后能恢复。

设计：
  - 任务状态存 ~/.hongjun/tasks/
  - 每个任务一个 JSON 文件
  - 任务完成后自动清理（保留最近N个供反思）
  - Gateway 重启后自动扫描并恢复未完成任务
"""

from __future__ import annotations
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from hongjun.logging_config import get_logger
from .planner import ExecutionPlan, TaskStatus

logger = get_logger("hongjun.task_state")

TASK_DIR = Path.home() / ".hongjun" / "tasks"
MAX_COMPLETED_TO_KEEP = 20


# ── 任务状态管理器 ──────────────────────────────────────────────────────────

class TaskStateManager:
    """
    管理任务状态的持久化和恢复。

    使用方式：
        mgr = TaskStateManager()
        mgr.save(plan)            # 保存任务状态
        mgr.load(plan_id)         # 恢复任务
        mgr.list_pending()         # 列出未完成任务
        mgr.cleanup(plan_id)       # 清理已完成任务
    """

    def __init__(self, task_dir: Path = TASK_DIR):
        self.task_dir = task_dir
        self.task_dir.mkdir(parents=True, exist_ok=True)

    # ── 保存/加载 ────────────────────────────────────────────────────────

    def save(self, plan: ExecutionPlan) -> str:
        """保存任务状态，返回文件路径"""
        # 转换 dataclass 为 dict（dataclass 不是 JSON 可序列化的）
        data = {
            "plan_id": plan.plan_id,
            "user_request": plan.user_request,
            "intent_type": plan.intent_type,
            "status": plan.status.value,
            "current_step": plan.current_step_index,
            "created_at": plan.created_at,
            "completed_at": plan.completed_at,
            "final_result": plan.final_result,
            "verification_result": plan.verification_result,
            "steps": [
                {
                    "step_id": s.step_id,
                    "description": s.description,
                    "tool_name": s.tool_name,
                    "tool_args": s.tool_args,
                    "verification": s.verification,
                    "status": s.status.value,
                    "result": s.result,
                    "error": s.error,
                    "retry_count": s.retry_count,
                    "max_retries": s.max_retries,
                    "executed_at": s.executed_at,
                }
                for s in plan.steps
            ],
            "saved_at": datetime.now().isoformat(),
        }

        file_path = self.task_dir / f"{plan.plan_id}.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 任务状态已保存 [{plan.plan_id}]: {file_path}")
        except Exception as e:
            logger.error(f"❌ 保存任务状态失败 [{plan.plan_id}]: {e}")

        return str(file_path)

    def load(self, plan_id: str) -> Optional[ExecutionPlan]:
        """根据 plan_id 加载任务状态"""
        file_path = self.task_dir / f"{plan_id}.json"
        if not file_path.exists():
            logger.warning(f"任务状态文件不存在 [{plan_id}]")
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 重建 ExecutionPlan
            from hongjun.planner import TaskStep, StepStatus, TaskStatus

            steps = [
                TaskStep(
                    step_id=s["step_id"],
                    description=s["description"],
                    tool_name=s.get("tool_name"),
                    tool_args=s.get("tool_args", {}),
                    verification=s.get("verification"),
                    status=StepStatus(s.get("status", "pending")),
                    result=s.get("result"),
                    error=s.get("error"),
                    retry_count=s.get("retry_count", 0),
                    max_retries=s.get("max_retries", 2),
                    executed_at=s.get("executed_at"),
                )
                for s in data.get("steps", [])
            ]

            plan = ExecutionPlan(
                plan_id=data["plan_id"],
                user_request=data["user_request"],
                intent_type=data["intent_type"],
                steps=steps,
                current_step_index=data.get("current_step", 0),
                status=TaskStatus(data.get("status", "planning")),
                created_at=data.get("created_at", datetime.now().isoformat()),
                completed_at=data.get("completed_at"),
                final_result=data.get("final_result"),
                verification_result=data.get("verification_result"),
            )
            logger.info(f"📂 任务状态已恢复 [{plan_id}]")
            return plan

        except Exception as e:
            logger.error(f"❌ 加载任务状态失败 [{plan_id}]: {e}")
            return None

    # ── 查询 ────────────────────────────────────────────────────────────

    def list_pending(self) -> list[dict]:
        """列出所有未完成的任务"""
        pending = []
        for f in self.task_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                status = data.get("status", "")
                if status in ("planning", "executing", "validating"):
                    data["file"] = str(f)
                    pending.append(data)
            except Exception:
                pass
        pending.sort(key=lambda x: x.get("created_at", ""))
        return pending

    def list_recent(self, limit: int = 10) -> list[dict]:
        """列出最近的任务（含已完成）"""
        files = sorted(self.task_dir.glob("*.json"), key=os.path.getmtime, reverse=True)
        results = []
        for f in files[:limit]:
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                results.append(data)
            except Exception:
                pass
        return results

    # ── 清理 ────────────────────────────────────────────────────────────

    def cleanup(self, plan_id: str):
        """清理已完成任务的状态文件"""
        file_path = self.task_dir / f"{plan_id}.json"
        if file_path.exists():
            try:
                # 先移到临时备份（方便问题溯源）
                backup_dir = self.task_dir / "completed"
                backup_dir.mkdir(exist_ok=True)
                shutil.move(str(file_path), str(backup_dir / f"{plan_id}.json"))
                logger.info(f"🗑️  已完成任务状态已归档 [{plan_id}]")
            except Exception as e:
                logger.error(f"❌ 清理任务状态失败 [{plan_id}]: {e}")

    def auto_cleanup(self, keep: int = MAX_COMPLETED_TO_KEEP):
        """
        自动清理：只保留最近 N 个已完成任务的状态文件。
        防止 ~/.hongjun/tasks/ 无限膨胀。
        """
        completed_dir = self.task_dir / "completed"
        if not completed_dir.exists():
            return

        files = sorted(completed_dir.glob("*.json"), key=os.path.getmtime, reverse=True)
        for f in files[keep:]:
            try:
                f.unlink()
                logger.debug(f"🗑️ 自动清理旧任务状态: {f.name}")
            except Exception:
                pass

    # ── 恢复扫描 ────────────────────────────────────────────────────────

    def scan_and_recover(self) -> list[ExecutionPlan]:
        """
        启动时扫描未完成任务，尝试恢复。

        Returns:
            需要继续执行的 ExecutionPlan 列表
        """
        pending = self.list_pending()
        recovered = []

        for p in pending:
            plan = self.load(p["plan_id"])
            if plan and plan.status in (TaskStatus.EXECUTING, TaskStatus.PLANNING, TaskStatus.VALIDATING):
                recovered.append(plan)
                logger.info(f"♻️  发现未完成任务 [{plan.plan_id}]: {plan.user_request[:50]}")

        return recovered
