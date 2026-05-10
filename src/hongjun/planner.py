"""
鸿钧 · 任务分解引擎
====================

将用户意图分解为可执行的子任务步骤，然后逐个执行并验证。

Plan-and-Execute 模式：
  1. Plan：先分析用户意图，生成子任务步骤列表
  2. Execute：逐个执行子任务，每步后验证
  3. Validate：所有步骤完成后交叉验证整体结果

特点：
  - 每步都有验证，不等到最后才发现问题
  - 子任务状态独立，单步失败不会丢失上下文
  - 支持从检查点恢复（任务状态持久化）
  - 结合反思引擎的经验，下次同类任务分解更准
"""

from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.planner")


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStatus(str, Enum):
    PLANNING = "planning"
    EXECUTING = "executing"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── 数据结构 ────────────────────────────────────────────────────────────────

@dataclass
class TaskStep:
    """单个子任务步骤"""
    step_id: str
    description: str          # 步骤描述（给人看的）
    tool_name: Optional[str]  # 要调用的工具名（如 "shell", "browser", "llm"）
    tool_args: dict = field(default_factory=dict)   # 工具参数
    verification: Optional[str] = None  # 验证条件（LLM判断用）
    status: StepStatus = StepStatus.PENDING
    result: Optional[str] = None       # 执行结果
    error: Optional[str] = None        # 错误信息
    retry_count: int = 0
    max_retries: int = 2
    executed_at: Optional[str] = None

    def mark_running(self):
        self.status = StepStatus.RUNNING
        self.executed_at = datetime.now().isoformat()

    def mark_completed(self, result: str):
        self.status = StepStatus.COMPLETED
        self.result = result

    def mark_failed(self, error: str):
        self.status = StepStatus.FAILED
        self.error = error

    def can_retry(self) -> bool:
        return self.retry_count < self.max_retries


@dataclass
class ExecutionPlan:
    """完整执行计划"""
    plan_id: str
    user_request: str
    intent_type: str
    steps: list[TaskStep]
    current_step_index: int = 0
    status: TaskStatus = TaskStatus.PLANNING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    final_result: Optional[str] = None
    verification_result: Optional[str] = None

    def current_step(self) -> Optional[TaskStep]:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def advance(self):
        self.current_step_index += 1

    def is_complete(self) -> bool:
        return all(s.status == StepStatus.COMPLETED for s in self.steps)

    def has_failed_steps(self) -> bool:
        return any(s.status == StepStatus.FAILED for s in self.steps)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "user_request": self.user_request,
            "intent_type": self.intent_type,
            "status": self.status.value,
            "current_step": self.current_step_index,
            "steps": [
                {
                    "step_id": s.step_id,
                    "description": s.description,
                    "tool_name": s.tool_name,
                    "status": s.status.value,
                    "result": s.result[:200] if s.result else None,
                    "error": s.error,
                    "retry_count": s.retry_count,
                }
                for s in self.steps
            ],
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "final_result": self.final_result[:500] if self.final_result else None,
        }


# ── 任务分解器 ──────────────────────────────────────────────────────────────

class TaskPlanner:
    """
    任务分解引擎：将用户请求拆解为可执行的步骤列表。

    使用方式：
        planner = TaskPlanner()
        plan = planner.create_plan("帮我开发一个博客系统")
        for step in plan.steps:
            result = executor.execute_step(step)
            if not verifier.verify(step):
                break
    """

    # 意图类型 → 典型步骤模板
    INTENT_STEP_TEMPLATES = {
        "code_generation": [
            {"description": "分析需求和技术方案", "tool_name": "llm", "verification": "是否明确了技术栈和实现方案"},
            {"description": "生成代码", "tool_name": "llm", "verification": "代码是否完整可运行"},
            {"description": "执行代码并验证输出", "tool_name": "executor", "verification": "执行是否成功且输出正确"},
            {"description": "交叉验证结果是否符合用户意图", "tool_name": "llm", "verification": "结果是否满足原始需求"},
        ],
        "search": [
            {"description": "理解搜索意图", "tool_name": "llm", "verification": "是否明确了搜索关键词"},
            {"description": "执行搜索", "tool_name": "browser", "verification": "是否获取到相关信息"},
            {"description": "提炼和验证搜索结果", "tool_name": "llm", "verification": "结果是否与需求相关"},
        ],
        "memory_recall": [
            {"description": "检索相关记忆", "tool_name": "memory", "verification": "是否找到相关记忆"},
            {"description": "验证记忆与当前任务的关联性", "tool_name": "llm", "verification": "记忆是否有用"},
        ],
        "general": [
            {"description": "理解用户意图", "tool_name": "llm", "verification": "意图是否清晰"},
            {"description": "执行任务", "tool_name": "llm", "verification": "任务是否完成"},
            {"description": "验证结果", "tool_name": "llm", "verification": "结果是否正确"},
        ],
    }

    def __init__(self):
        pass

    def create_plan(
        self,
        user_request: str,
        intent_type: str,
        custom_steps: list[dict] = None,
    ) -> ExecutionPlan:
        """
        创建执行计划。

        Args:
            user_request: 用户原始请求
            intent_type: 意图类型（来自 intent_classifier）
            custom_steps: 可选的自定义步骤（覆盖模板）

        Returns:
            ExecutionPlan 对象
        """
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        template = self.INTENT_STEP_TEMPLATES.get(
            intent_type, self.INTENT_STEP_TEMPLATES["general"]
        )
        steps_def = custom_steps if custom_steps else template

        steps = [
            TaskStep(
                step_id=f"{plan_id}_step_{i}",
                description=s["description"],
                tool_name=s.get("tool_name"),
                tool_args=s.get("tool_args", {}),
                verification=s.get("verification"),
            )
            for i, s in enumerate(steps_def)
        ]

        plan = ExecutionPlan(
            plan_id=plan_id,
            user_request=user_request,
            intent_type=intent_type,
            steps=steps,
        )
        logger.info(f"📋 创建执行计划 [{plan_id}]: {len(steps)} 个步骤")
        return plan

    def resume_plan(self, plan_data: dict) -> ExecutionPlan:
        """
        从持久化的 plan dict 恢复执行计划。
        """
        plan_id = plan_data["plan_id"]
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
            )
            for i, s in enumerate(plan_data.get("steps", []))
        ]
        return ExecutionPlan(
            plan_id=plan_id,
            user_request=plan_data["user_request"],
            intent_type=plan_data["intent_type"],
            steps=steps,
            current_step_index=plan_data.get("current_step", 0),
            status=TaskStatus(plan_data.get("status", "planning")),
            created_at=plan_data.get("created_at", datetime.now().isoformat()),
            completed_at=plan_data.get("completed_at"),
            final_result=plan_data.get("final_result"),
        )
