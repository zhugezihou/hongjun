"""
mempalace-research Skill 实现
=============================

通过 github-ops 读取 MemPalace 源码，研究其记忆系统架构。
"""
import sys
sys.path.insert(0, "/home/asus/hongjun/skills/github-ops")

from github_ops import repo_file, tree, code_search, search


def tree_mempalace() -> str:
    """获取 MemPalace 仓库目录结构"""
    return tree(owner="MemPalace", repo_name="mempalace", ref="develop")


def readme() -> str:
    """读取 MemPalace README"""
    return repo_file(owner="MemPalace", repo_name="mempalace", path="README.md", ref="develop")


def layers() -> str:
    """读取 4层记忆栈实现（layers.py）"""
    return repo_file(owner="MemPalace", repo_name="mempalace", path="mempalace/layers.py", ref="develop")


def backends() -> str:
    """读取存储后端接口（backends/base.py）"""
    return repo_file(owner="MemPalace", repo_name="mempalace", path="mempalace/backends/base.py", ref="develop")


def palace() -> str:
    """读取 Palace 收藏库实现（palace.py）"""
    return repo_file(owner="MemPalace", repo_name="mempalace", path="mempalace/palace.py", ref="develop")


def searcher() -> str:
    """读取混合搜索实现（searcher.py）"""
    return repo_file(owner="MemPalace", repo_name="mempalace", path="mempalace/searcher.py", ref="develop")


def knowledge_graph() -> str:
    """读取时序知识图谱实现（knowledge_graph.py）"""
    return repo_file(owner="MemPalace", repo_name="mempalace", path="mempalace/knowledge_graph.py", ref="develop")


def mcp_server() -> str:
    """读取 MCP 服务端实现（mcp_server.py）"""
    return repo_file(owner="MemPalace", repo_name="mempalace", path="mempalace/mcp_server.py", ref="develop")


def full_research() -> str:
    """完整研究：综合 README + layers.py"""
    r1 = readme()
    r2 = layers()
    return f"{r1[:4000]}\n\n---\n{r2[:4000]}"
