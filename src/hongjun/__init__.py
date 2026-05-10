"""
鸿钧 · Agent 系统
==================

核心模块：
  orchestrator  — 任务编排 / 意图解析 / 调度汇总
  memory        — 三层记忆系统 / MemPalace / SQLite 持久化
  tools         — 工具注册中心 / 浏览器 / 搜索 / Shell
  executor      — 代码生成执行 / ReAct 循环
  security      — 输入输出安全过滤
  evaluator     — 回复质量评估

MCP 端口：
  HongjunMCPServer: 20786（stdio）/ HTTP SSE

鸿钧 Gateway: 20830（HTTP REST）
"""

__version__ = "0.2.0"

from .orchestrator import CoordinatorState, coordinator_graph
from .memory import HongjunMemory

__all__ = [
    "coordinator_graph",
    "CoordinatorState",
    "HongjunMemory",
]
