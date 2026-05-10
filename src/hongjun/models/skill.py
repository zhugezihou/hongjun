"""
礼部 · Skill 数据模型
=====================

Skill 和 ToolFunction Pydantic 模型。
参考 pydantic-ai / Qwen-Agent 的工具注册表设计。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Callable, Any

from pydantic import BaseModel, Field, ConfigDict


class Skill(BaseModel):
    """
    Skill 定义模型。

    来自 SKILL.md 的 YAML frontmatter + 解析出的执行函数。
    替代 skill_manager.py 中的@dataclass Skill。
    """
    model_config = ConfigDict(extra="allow")

    # ── 基本信息 ─────────────────────────────────────────────────────
    name: str                           # 唯一标识（如 "web-scraper"）
    description: str = ""               # 功能描述（供 LLM 理解何时调用）
    triggers: list[str] = Field(default_factory=list)  # 触发关键词列表
    category: str = "general"            # 分类：web / devops / data / ...
    version: str = "1.0"
    author: str = "unknown"

    # ── 依赖 ─────────────────────────────────────────────────────────
    dependencies: list[str] = Field(default_factory=list)   # pip 包依赖
    required_tools: list[str] = Field(default_factory=list)  # 依赖的基础工具

    # ── 路径 ─────────────────────────────────────────────────────────
    root_dir: Optional[str] = None       # 所在目录

    # ── 动态属性（不参与序列化）──────────────────────────────────────
    functions: dict[str, Callable] = Field(default_factory=dict, exclude=True)

    # ── 匹配方法 ─────────────────────────────────────────────────────

    def match_score(self, query: str) -> float:
        """
        计算 query 与这个 skill 的匹配度。

        策略：
        - 精确匹配触发词：+1.0
        - query 包含触发词：+0.7
        - description 关键词匹配：+0.3
        - category 匹配：+0.1
        """
        if not query:
            return 0.0

        import re
        query_lower = query.lower()
        score = 0.0

        # 触发词匹配
        for trigger in self.triggers:
            trigger_lower = trigger.lower().strip('" ')
            if trigger_lower == query_lower:
                score = max(score, 1.0)
            elif trigger_lower in query_lower:
                score = max(score, 0.7)
            elif query_lower in trigger_lower:
                score = max(score, 0.5)

        # description 关键词匹配
        desc_lower = self.description.lower()
        words = re.findall(r'\w+', query_lower)
        for word in words:
            if len(word) > 2 and word in desc_lower:
                score = max(score, score + 0.1)

        # 分类加分
        if any(cat in query_lower for cat in [self.category, "skill"]):
            score += 0.1

        return min(score, 1.0)


class ToolFunction(BaseModel):
    """
    工具函数注册模型。

    用于 ToolRegistry（参考 Qwen-Agent 的 function_list 设计）。
    """
    name: str                           # 唯一名称（e.g. "skill_web-scraper__scrape"）
    func: Callable = Field(exclude=True)  # 实际函数对象
    description: str = ""               # 描述（供 LLM 决定何时调用）
    parameters: dict[str, Any] = Field(default_factory=dict)  # 参数 schema hint

    def call(self, **kwargs) -> Any:
        """执行工具函数"""
        return self.func(**kwargs)
