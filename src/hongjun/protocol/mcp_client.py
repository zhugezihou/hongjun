"""
礼部 · MCP Client
==================

Model Context Protocol (MCP) 客户端。

职责：
  - 连接外部 MCP 服务器（stdio / HTTP）
  - 调用远程 MCP 工具
  - 将 MCP 工具接入 ToolRegistry

支持两种连接方式：
  - StdioServerParameters：本地进程（如 `npx -y @modelcontextprotocol/server-filesystem`）
  - HTTP：远程 MCP 服务器（streamable_http）

用法：
    # 连接 filesystem MCP 服务器
    client = MCPClient()
    client.connect_stdio(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    )
    tools = client.list_tools()
    result = client.call_tool("list_directory", {"path": "/tmp"})
    client.disconnect()

    # 接入 ToolRegistry
    from hongjun.tools import TOOL_REGISTRY
    mcp_tools = client.discover_and_register(TOOL_REGISTRY, prefix="mcp_fs")
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Dict, List, Optional, Union
from dataclasses import dataclass, field

from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.session import ClientSession
from mcp.types import Tool, CallToolResult

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.mcp.client")


# ── MCP 服务器配置 ────────────────────────────────────────────────────────────

@dataclass
class MCPServerConfig:
    """单个 MCP 服务器的配置"""
    name: str                      # 服务器名称（如 "filesystem"）
    command: str                   # 启动命令（如 "npx", "python"）
    args: List[str] = field(default_factory=list)   # 命令参数
    env: Optional[Dict[str, str]] = None            # 环境变量
    cwd: Optional[str] = None                       # 工作目录


# ── MCP Client ─────────────────────────────────────────────────────────────────

class MCPClient:
    """
    MCP 客户端，管理一个或多个 MCP 服务器连接。

    用法：
        client = MCPClient()
        client.connect_stdio(command="npx", args=["-y", "server-filesystem", "/tmp"])
        tools = client.list_tools()
        result = client.call_tool("list_directory", {"path": "/tmp"})
    """

    def __init__(self):
        self._sessions: Dict[str, ClientSession] = {}
        self._servers: Dict[str, MCPServerConfig] = {}
        self._server_tools: Dict[str, List[Tool]] = {}  # server_name → tools

    # ── 连接管理 ─────────────────────────────────────────────────────────────

    async def _create_session(
        self,
        server_name: str,
        params: StdioServerParameters,
    ) -> ClientSession:
        """建立 stdio 连接"""
        if server_name in self._sessions:
            await self._sessions[server_name].close()
            del self._sessions[server_name]

        client = stdio_client(params)
        async with client as (read, write):
            session = ClientSession(read, write)
            await session.initialize()
            self._sessions[server_name] = session
            return session

    def connect_stdio(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> "MCPClient":
        """
        连接本地 MCP 服务器（stdio）。

        Args:
            name: 服务器名称（用于区分多服务器场景）
            command: 启动命令
            args: 命令参数列表
            env: 环境变量
            cwd: 工作目录

        用法：
            client.connect_stdio(
                name="filesystem",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            )
        """
        args = args or []
        config = MCPServerConfig(name=name, command=command, args=args, env=env, cwd=cwd)
        self._servers[name] = config

        params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
            cwd=cwd,
        )

        # 在新事件循环中初始化
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果已经在事件循环中，创建任务
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self._create_session(name, params)
                    )
                    # 阻塞等待（简化为同步处理）
        except RuntimeError:
            # 没有事件循环，直接创建
            pass

        # 同步初始化（简化处理，实际在下方 async def 中）
        asyncio.run(self._create_session(name, params))
        return self

    def connect_http(
        self,
        name: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> "MCPClient":
        """
        连接远程 MCP 服务器（HTTP）。

        Args:
            name: 服务器名称
            url: 服务器 URL（如 "http://localhost:3000/mcp"）
            headers: HTTP 请求头
        """
        # TODO: 实现 HTTP transport
        raise NotImplementedError("HTTP transport not yet implemented")

    def disconnect(self, name: Optional[str] = None) -> None:
        """
        断开连接。

        Args:
            name: 服务器名称（None 表示断开所有）
        """
        if name:
            if name in self._sessions:
                asyncio.run(self._sessions[name].close())
                del self._sessions[name]
            return

        for session in self._sessions.values():
            try:
                asyncio.run(session.close())
            except Exception:
                pass
        self._sessions.clear()

    # ── 工具操作 ─────────────────────────────────────────────────────────────

    def list_tools(self, server_name: Optional[str] = None) -> List[Tool]:
        """
        列出已连接服务器的 MCP 工具。

        Args:
            server_name: 服务器名称（None 表示所有服务器）

        Returns:
            List[Tool] - MCP Tool 对象列表
        """
        if server_name:
            return self._server_tools.get(server_name, [])

        result = []
        for tools in self._server_tools.values():
            result.extend(tools)
        return result

    async def _list_tools_async(self, server_name: str) -> List[Tool]:
        """异步列出工具"""
        session = self._sessions.get(server_name)
        if not session:
            return []
        try:
            response = await session.list_tools()
            return response.tools
        except Exception as e:
            logger.warning("mcp_list_tools_error", server=server_name, error=str(e))
            return []

    def discover_tools(self) -> Dict[str, List[Tool]]:
        """
        发现所有已连接服务器的 MCP 工具。

        Returns:
            {server_name: [Tool, ...]}
        """
        discovered = {}
        for name in self._servers:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            tools = loop.run_until_complete(self._list_tools_async(name))
            if tools:
                discovered[name] = tools
                self._server_tools[name] = tools
        return discovered

    async def _call_tool_async(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> CallToolResult:
        """异步调用工具"""
        session = self._sessions.get(server_name)
        if not session:
            raise ValueError(f"MCP server '{server_name}' not connected")

        result = await session.call_tool(tool_name, arguments)
        return result

    def call_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        server_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        同步调用 MCP 工具。

        Args:
            tool_name: MCP 工具名称
            arguments: 工具参数
            server_name: 服务器名称（多服务器时必须指定）

        Returns:
            {"content": str, "isError": bool}
        """
        arguments = arguments or {}

        # 如果未指定 server_name，在所有已连接服务器中查找
        if not server_name:
            for name, tools in self._server_tools.items():
                if any(t.name == tool_name for t in tools):
                    server_name = name
                    break
            if not server_name:
                raise ValueError(f"MCP tool '{tool_name}' not found in any connected server")

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        result = loop.run_until_complete(
            self._call_tool_async(server_name, tool_name, arguments)
        )

        # 标准化返回格式
        content_parts = []
        is_error = result.isError if hasattr(result, 'isError') else False

        if hasattr(result, 'content') and result.content:
            for part in result.content:
                if hasattr(part, 'text'):
                    content_parts.append(part.text)
                elif hasattr(part, 'data'):
                    content_parts.append(str(part.data))
                else:
                    content_parts.append(str(part))

        return {
            "content": "\n".join(content_parts),
            "isError": is_error,
            "meta": result.meta if hasattr(result, 'meta') else None,
        }

    # ── ToolRegistry 接入 ────────────────────────────────────────────────────

    def register_to_registry(
        self,
        registry,
        server_name: str,
        prefix: str = "mcp",
    ) -> int:
        """
        将 MCP 服务器的工具注册到 ToolRegistry。

        Args:
            registry: ToolRegistry 实例
            server_name: 服务器名称
            prefix: 注册到 ToolRegistry 的工具名前缀（如 "mcp_filesystem"）

        Returns:
            注册的工具数量
        """
        tools = self._server_tools.get(server_name, [])
        count = 0

        for tool in tools:
            tool_name = f"{prefix}_{tool.name}"
            # 创建包装函数
            wrapped = self._make_tool_wrapper(server_name, tool.name)
            try:
                registry.register(
                    name=tool_name,
                    func=wrapped,
                    description=tool.description or f"MCP tool: {tool.name}",
                    category="mcp",
                )
                count += 1
            except ValueError as e:
                # 工具已存在，跳过
                pass

        return count

    def _make_tool_wrapper(self, server_name: str, tool_name: str) -> Callable:
        """为 MCP 工具创建包装函数"""
        def wrapper(**kwargs) -> str:
            result = self.call_tool(tool_name, kwargs, server_name=server_name)
            if result.get("isError"):
                return f"❌ MCP tool error: {result['content']}"
            return result.get("content", "")
        return wrapper

    # ── 上下文管理 ──────────────────────────────────────────────────────────

    def __enter__(self) -> "MCPClient":
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()


# ── 全局 MCP Client 实例 ────────────────────────────────────────────────────────

MCP_CLIENT = MCPClient()


# ── 常用 MCP 服务器快速连接 ────────────────────────────────────────────────────

def connect_filesystem(root: str = "/", prefix: str = "mcp_fs") -> MCPClient:
    """
    快速连接官方 filesystem MCP 服务器。

    需要安装：
        npm install -g @modelcontextprotocol/server-filesystem

    用法：
        client = connect_filesystem("/tmp")
        client.register_to_registry(TOOL_REGISTRY, "filesystem", "mcp_fs")
    """
    client = MCPClient()

    # 尝试不同启动方式
    import shutil
    if shutil.which("npx"):
        command = "npx"
        args = ["-y", "@modelcontextprotocol/server-filesystem", root]
    elif shutil.which("node"):
        command = "node"
        args = ["-e", f"require('@modelcontextprotocol/server-filesystem')"]
    else:
        raise RuntimeError("Neither npx nor node is available for MCP filesystem server")

    try:
        client.connect_stdio(name="filesystem", command=command, args=args)
    except Exception as e:
        print(f"[MCP] Failed to connect filesystem server: {e}")
        raise

    return client
