"""
鸿钧 · Agent 系统
==================

核心模块：
  orchestrator      — 任务编排 / 意图解析 / 调度汇总
  memory            — 三层记忆系统 / MemPalace / SQLite 持久化
  tools             — 工具注册中心 / 浏览器 / 搜索 / Shell
  executor          — 代码生成执行 / ReAct 循环
  security          — 输入输出安全过滤
  evaluator         — 回复质量评估

新增（阶段1）：
  planner           — Plan-and-Execute 任务分解引擎
  task_executor     — 执行器（含交叉验证闭环）
  task_state        — 任务状态持久化，支持中断恢复
  memory_injection  — 每次LLM调用前注入相关记忆上下文
  reflection_engine — 定期复盘，巩固正确经验/遗忘错误经验
  evolution_memory  — 进化记忆系统（集成反思引擎钩子）

新增（阶段2）：
  error_pattern     — 错误模式积累：错误类型→修复方案映射库
  skill_discovery   — 主动技能发现：定期搜索 GitHub trending

新增（阶段3）：
  meta_learner      — 元学习：根据任务特征选择最佳策略
  self_improver     — 主动改进：基于反思+错误模式优化自身代码

MCP 端口：
  HongjunMCPServer: 20786（stdio）/ HTTP SSE

鸿钧 Gateway: 20830（HTTP REST）
"""

__version__ = "0.4.0"
__all__ = [
    "coordinator_graph",
    "CoordinatorState",
    "HongjunMemory",
]
from .orchestrator import CoordinatorState, coordinator_graph
from .memory import HongjunMemory
