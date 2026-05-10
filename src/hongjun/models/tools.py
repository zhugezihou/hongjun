"""
礼部 · 工具数据模型
==================

标准化工具定义（参考 Qwen-Agent / pydantic-ai 的工具注册表设计）。
支持三种注册模式：字符串（名称查找）/ 字典（内联定义）/ BaseTool（对象）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Union
from pydantic import BaseModel, Field, ConfigDict


class ToolParam(BaseModel):
    """单个工具参数的 schema（精简版 OpenAI function schema）"""
    name: str
    description: str = ""
    type: str = "string"
    default: Optional[str] = None


class Tool(BaseModel):
    """
    标准化工具定义。

    对应 OpenAI function calling schema，供 LLM 理解工具用途和参数。
    """
    model_config = ConfigDict(extra="allow")

    name: str                          # 唯一名称
    description: str = ""              # 描述（供 LLM 决定何时调用）
    parameters: list[ToolParam] = Field(default_factory=list)  # 参数列表
    category: str = "general"          # 分类：web / devops / file / ...
    is_async: bool = False             # 是否异步工具

    def to_openai_schema(self) -> dict:
        """
        转换为 OpenAI function calling schema。

        用法：
            schema = tool.to_openai_schema()
            # → {"name": "...", "description": "...", "parameters": {"type": "object", "properties": {...}}}
        """
        props = {}
        required = []
        for p in self.parameters:
            props[p.name] = {
                "type": p.type,
                "description": p.description,
            }
            if p.default is None:
                required.append(p.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        }


class ToolResult(BaseModel):
    """
    工具执行结果。

    替代 tools.py 中的 @dataclass ToolResult。
    """
    model_config = ConfigDict(use_enum_values=True)

    tool_name: str
    status: str = "success"          # success | failed | timeout | unavailable
    content: Any = None
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    metadata: dict = Field(default_factory=dict)

    def to_dict(self) -> dict:
        """转换为 dict"""
        return self.model_dump(mode="json")

    def is_success(self) -> bool:
        return self.status == "success"
