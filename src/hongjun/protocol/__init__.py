"""
鸿钧协议层
==========

| 协议 | 文件 | 用途 |
|------|------|------|
| MCP | mcp_client.py / mcp_server.py | LLM ↔ 工具标准化协议 |

MCP（Model Context Protocol）：
  - MCP Server：将鸿钧工具暴露为标准 MCP 工具（供 Claude Desktop / 其他 MCP 客户端使用）
  - MCP Client：连接外部 MCP 服务器，将远程工具接入 ToolRegistry
"""

from .mcp_client import (
    MCPClient,
    MCPServerConfig,
    MCP_CLIENT,
    connect_filesystem,
)

from .mcp_server import (
    HongjunMCPServer,
    create_mcp_server,
)

__all__ = [
    # MCP
    "MCPClient",
    "MCPServerConfig",
    "MCP_CLIENT",
    "connect_filesystem",
    "HongjunMCPServer",
    "create_mcp_server",
]
