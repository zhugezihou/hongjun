"""
工部 · MCP Server
==================

Model Context Protocol (MCP) 服务器。

职责：
  - 将鸿钧的工具/技能暴露为标准 MCP 服务器
  - 支持 stdio / SSE / streamable_http 三种传输方式
  - 复用 ToolRegistry 中的已有工具

用法：
    # 以 stdio 方式运行（供 Claude Desktop 使用）
    server = HongjunMCPServer()
    server.run_stdio()

    # 以 HTTP 方式运行（供远程 MCP 客户端使用）
    server.run_streamable_http(port=20831)

    # 在 FastMCP 上注册工具
    @server.tool()
    def my_tool(arg1: str) -> str:
        return f"Hello {arg1}"
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Dict, List, Optional, Union

from mcp.server import FastMCP, Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, CallToolResult

from ..logging_config import get_logger
from ..tools import TOOL_REGISTRY

logger = get_logger("hongjun.mcp.server")


class HongjunMCPServer:
    """
    鸿钧 MCP 服务器。

    基于 FastMCP，将鸿钧的 ToolRegistry 工具暴露为标准 MCP 工具。
    支持三种传输方式：
      - run_stdio()        ：stdio（Claude Desktop 原生支持）
      - run_sse_async()    ：Server-Sent Events
      - run_streamable_http_async() ：HTTP 长连接
    """

    def __init__(
        self,
        name: str = "Hongjun",
        instructions: Optional[str] = None,
    ):
        """
        Args:
            name: MCP 服务器名称
            instructions: 服务器说明（会发给 LLM）
        """
        self.name = name
        self.instructions = instructions or (
            "Hongjun AI Agent - 自主 AI Agent 系统，"
            "支持 Shell 命令、文件读写、网页浏览、GitHub 操作、浏览器自动化等功能。"
        )

        # 创建 FastMCP 实例
        self._mcp = FastMCP(name, instructions=self.instructions)

        # 已注册的鸿钧工具名 → MCP 工具名
        self._registered_tools: Dict[str, str] = {}

    # ── 工具注册 ─────────────────────────────────────────────────────────────

    def register_tool(self, tool_name: str, description: Optional[str] = None) -> None:
        """
        注册 ToolRegistry 中的工具为 MCP 工具。

        Args:
            tool_name: ToolRegistry 中的工具名称
            description: MCP 工具描述（默认使用 ToolRegistry 中的描述）
        """
        tool = TOOL_REGISTRY.get(tool_name)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found in ToolRegistry")

        description = description or tool.description

        # 动态创建函数，用 FastMCP.tool() 装饰
        func = TOOL_REGISTRY.get_func(tool_name)
        if func is None:
            raise ValueError(f"Tool '{tool_name}' has no bound function")

        self._mcp.add_tool(func, name=tool_name, description=description)
        self._registered_tools[tool_name] = tool_name

    def register_all_tools(
        self,
        tool_names: Optional[List[str]] = None,
        category: Optional[str] = None,
    ) -> int:
        """
        批量注册工具到 MCP 服务器。

        Args:
            tool_names: 指定工具名列表（None 表示全部）
            category: 只注册指定分类的工具（如 "mcp", "general"）

        Returns:
            注册数量
        """
        if tool_names:
            tools = [TOOL_REGISTRY.get(name) for name in tool_names]
        elif category:
            tools = [t for t in TOOL_REGISTRY.list_tools() if t.category == category]
        else:
            tools = TOOL_REGISTRY.list_tools()

        count = 0
        for tool in tools:
            if tool is None:
                continue
            try:
                self.register_tool(tool.name)
                count += 1
            except Exception as e:
                logger.warning("mcp_tool_register_failed", tool=tool.name, error=str(e))

        return count

    def add_tool(
        self,
        func: Callable,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        """
        直接添加自定义函数为 MCP 工具（装饰器语法糖）。

        用法：
            @server.add_tool(description="xxx")
            def my_tool(arg1: str) -> str:
                return f"result: {arg1}"

        等价于：
            @server.tool()
            def my_tool(...) -> ...
        """
        self._mcp.add_tool(func, name=name, description=description)

    def tool(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Callable:
        """
        装饰器：添加 MCP 工具。

        用法：
            @server.tool(description="这是一个加法工具")
            def add(a: int, b: int) -> int:
                return a + b
        """
        def decorator(func: Callable) -> Callable:
            self._mcp.add_tool(func, name=name, description=description)
            return func
        return decorator

    # ── 生命周期 ─────────────────────────────────────────────────────────────

    def run_stdio(self) -> None:
        """以 stdio 方式运行（阻塞）"""
        logger.info("mcp_server_starting_stdio", server_name=self.name)
        self._mcp.run(transport="stdio")

    async def run_sse_async(
        self,
        host: str = "localhost",
        port: int = 20831,
    ) -> None:
        """以 SSE 方式运行（异步）"""
        logger.info("mcp_server_starting_sse", host=host, port=port)
        await self._mcp.run_sse_async(host=host, port=port)

    async def run_streamable_http_async(
        self,
        host: str = "localhost",
        port: int = 20831,
        path: str = "/mcp",
    ) -> None:
        """以 HTTP Streamable 方式运行（异步）"""
        import uvicorn
        logger.info("mcp_server_starting_http", host=host, port=port, path=path)
        # 新版 FastMCP.run_streamable_http_async() 不接受 host/port 参数，
        # 通过 streamable_http_app 获取 Starlette app，再用 uvicorn 托管
        app = self._mcp.streamable_http_app()
        config = uvicorn.Config(app, host=host, port=port, root_path=path, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    # ── 独立进程入口 ─────────────────────────────────────────────────────────

    @property
    def streamable_http_app(self):
        """获取 ASGI app（用于嵌入到其他服务器）"""
        return self._mcp.streamable_http_app

    def __call__(self):
        """支持作为 ASGI app 使用"""
        return self._mcp.streamable_http_app


# ── 独立 MCP Server 入口 ────────────────────────────────────────────────────────

def create_mcp_server() -> HongjunMCPServer:
    """
    创建并配置好工具的 MCP 服务器。

    自动注册 ToolRegistry 中的所有工具（含 Skills）。
    """
    server = HongjunMCPServer(
        name="Hongjun",
        instructions="鸿钧 AI Agent — 自包含 Agent 系统，支持 Shell、文件操作、网页浏览、GitHub 操作等工具。"
    )

    # 先初始化 Skills 系统
    from ..tools import init_skills
    init_skills()

    # 注册所有工具
    count = server.register_all_tools()
    logger.info("mcp_server_tools_registered", count=count)

    return server


if __name__ == "__main__":
    # 独立运行：stdio 模式
    server = create_mcp_server()
    server.run_stdio()
