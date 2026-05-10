"""
工部 · 核心数据模型统一出口
============================

所有 Pydantic 模型统一导出，外部 import 路径：
    from hongjun.models import Session, Message, SessionState, Skill, Task, CronJob

用法：
    session = Session(id="abc", platform="feishu")
    session.add_message("user", "你好")
"""

from .message import Message, MessageRole
from .session import Session, SessionState
from .skill import Skill, ToolFunction
from .task import Task, TaskStatus, TaskType
from .cron import CronJob, CronJobStatus
from .tools import Tool, ToolParam, ToolResult

__all__ = [
    # message
    "Message",
    "MessageRole",
    # session
    "Session",
    "SessionState",
    # skill
    "Skill",
    "ToolFunction",
    # task
    "Task",
    "TaskStatus",
    "TaskType",
    # cron
    "CronJob",
    "CronJobStatus",
    # tools
    "Tool",
    "ToolParam",
    "ToolResult",
]
