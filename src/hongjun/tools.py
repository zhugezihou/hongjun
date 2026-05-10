"""
礼部 · 工具层
==============

鸿钧 Agent 的能力扩展层。

职责：
  - 注册和管理所有工具
  - 统一工具调用接口
  - 工具执行结果标准化
  - 提供 OpenAI function calling schema（供 LLM 工具调用）

支持三种注册模式（参考 Qwen-Agent）：
  - 字符串：工具名称（已注册工具的别名）
  - 字典：内联工具定义 {"name": ..., "description": ..., "parameters": {...}}
  - Tool 对象：带完整 schema 的工具对象

已集成工具：
  - BrowserTool      : 浏览器自动化（browser-use）
  - SearchTool       : 网页搜索（Jina AI / Tavily）
  - ShellTool        : Shell 命令执行
  - FileTool         : 文件读写操作
  - WebFetchTool     : 网页内容抓取
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Callable, Dict, List, Optional, Union

from .models.tools import Tool, ToolParam, ToolResult
from hongjun.logging_config import get_logger

logger = get_logger("hongjun.tools")


class ToolRegistry:
    """
    工具注册中心（礼部尚书）

    所有工具通过这里注册和调用。
    支持同步和异步两种调用方式。

    三种注册模式（参考 Qwen-Agent）：
        registry.register("shell")                              # 字符串：别名
        registry.register(name="xxx", func=my_func)            # 对象：直接注册
        registry.register(name="xxx", func=my_func,
                          description="...",
                          parameters=[ToolParam(name="arg", description="...")])

    function_list 批量注册：
        registry.load([
            "shell",                                      # 字符串
            {"name": "my_tool", "description": "...",    # 字典
             "parameters": [{"name": "x", "type": "integer"}]},
            my_tool_object,                               # Tool 对象
        ])
    """

    def __init__(self):
        # tool_name → Tool 元数据
        self._tools: Dict[str, Tool] = {}
        # tool_name → Callable 实际函数
        self._funcs: Dict[str, Callable] = {}
        # 别名表：alias → 真实 tool_name
        self._aliases: Dict[str, str] = {}

    # ── 注册 ────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        func: Optional[Callable] = None,
        description: str = "",
        parameters: Optional[List[ToolParam]] = None,
        category: str = "general",
        alias_of: Optional[str] = None,
    ) -> None:
        """
        注册工具。

        用法：
            # 方式1：直接注册函数，自动从 signature 提取参数
            registry.register("shell", shell_tool)

            # 方式2：带完整 schema
            registry.register(
                name="web_search",
                func=search_tool,
                description="搜索网页",
                parameters=[
                    ToolParam(name="query", description="搜索词", type="string"),
                ],
            )

            # 方式3：别名（指向已注册工具）
            registry.register("ls", alias_of="shell")
        """
        if alias_of:
            # 别名模式
            self._aliases[name] = alias_of
            return

        if func is None:
            raise ValueError(f"register '{name}': func is required (unless using alias_of)")

        # 从函数 signature 自动提取参数（如果没有提供 schema）
        if parameters is None:
            parameters = self._extract_params(func)

        tool = Tool(
            name=name,
            description=description or self._func_doc(func),
            parameters=parameters,
            category=category,
            is_async=asyncio.iscoroutinefunction(func),
        )
        self._tools[name] = tool
        self._funcs[name] = func

    def load(self, function_list: List[Union[str, Dict, Tool, Callable]]) -> None:
        """
        批量加载工具（参考 Qwen-Agent Agent.__init__）。

        支持：
          - str          : 工具名称（已注册或内置）
          - dict         : 内联工具定义
          - Tool         : Tool 对象
          - Callable     : 直接注册函数
        """
        for item in function_list:
            if isinstance(item, str):
                # 字符串：注册别名或跳过（内置工具后续 init 时注册）
                pass
            elif isinstance(item, dict):
                self._register_from_dict(item)
            elif isinstance(item, Tool):
                self._tools[item.name] = item
            elif callable(item):
                name = getattr(item, "__name__", None) or getattr(item, "name", "unknown")
                self.register(name, item)
            else:
                raise TypeError(f"Unsupported tool type: {type(item)}")

    def _register_from_dict(self, d: Dict) -> None:
        """从字典注册工具（内联定义）"""
        name = d.get("name")
        if not name:
            raise ValueError(f"Tool dict missing 'name': {d}")
        params = [
            ToolParam(
                name=p["name"],
                description=p.get("description", ""),
                type=p.get("type", "string"),
                default=p.get("default"),
            )
            for p in d.get("parameters", [])
        ]
        tool = Tool(
            name=name,
            description=d.get("description", ""),
            parameters=params,
            category=d.get("category", "general"),
        )
        self._tools[name] = tool
        # 注意：dict 模式不绑定函数，需要后续 set_func()

    def set_func(self, name: str, func: Callable) -> None:
        """为已注册工具设置/覆盖执行函数"""
        self._funcs[name] = func

    # ── 查询 ────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[Tool]:
        """获取工具定义"""
        if name in self._aliases:
            return self._tools.get(self._aliases[name])
        return self._tools.get(name)

    def get_func(self, name: str) -> Optional[Callable]:
        """获取工具函数"""
        if name in self._aliases:
            return self._funcs.get(self._aliases[name])
        return self._funcs.get(name)

    def list_tools(self) -> List[Tool]:
        """列出所有已注册工具"""
        return list(self._tools.values())

    def get_names(self) -> List[str]:
        """列出所有工具名称（含别名）"""
        names = list(self._tools.keys())
        names.extend(self._aliases.keys())
        return names

    # ── OpenAI Function Calling Schema ─────────────────────────────

    def get_openai_functions(self) -> List[Dict]:
        """
        获取所有工具的 OpenAI function calling schema。

        用法（传给 LLM）：
            functions = registry.get_openai_functions()
            # → [{"type": "function", "function": {"name": "shell", ...}}, ...]
        """
        return [t.to_openai_schema() for t in self._tools.values()]

    def get_tools_for_llm(self) -> List[Dict]:
        """
        获取供 LLM 使用的工具列表（兼容旧 API）。

        等价于 get_openai_functions()。
        """
        return self.get_openai_functions()

    # ── 调用 ────────────────────────────────────────────────────────

    def call(self, name: str, **kwargs) -> ToolResult:
        """
        同步调用工具。

        用法：
            result = registry.call("shell", command="ls -la")
        """
        start = time.time()
        resolved = self._resolve(name)
        if not resolved:
            return ToolResult(
                tool_name=name,
                status="unavailable",
                content=None,
                error=f"工具 {name} 不存在",
            )

        tool_name, func = resolved
        if func is None:
            return ToolResult(
                tool_name=tool_name,
                status="unavailable",
                content=None,
                error=f"工具 {tool_name} 未绑定执行函数",
            )

        try:
            content = func(**kwargs)
            return ToolResult(
                tool_name=tool_name,
                status="success",
                content=content,
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return ToolResult(
                tool_name=tool_name,
                status="failed",
                content=None,
                error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    async def call_async(self, name: str, **kwargs) -> ToolResult:
        """异步调用工具"""
        start = time.time()
        resolved = self._resolve(name)
        if not resolved:
            return ToolResult(
                tool_name=name,
                status="unavailable",
                content=None,
                error=f"工具 {name} 不存在",
            )

        tool_name, func = resolved
        if func is None:
            return ToolResult(
                tool_name=tool_name,
                status="unavailable",
                content=None,
                error=f"工具 {tool_name} 未绑定执行函数",
            )

        try:
            if asyncio.iscoroutinefunction(func):
                content = await func(**kwargs)
            else:
                content = await asyncio.to_thread(func, **kwargs)
            return ToolResult(
                tool_name=tool_name,
                status="success",
                content=content,
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return ToolResult(
                tool_name=tool_name,
                status="failed",
                content=None,
                error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    # ── 内部辅助 ───────────────────────────────────────────────────

    def _resolve(self, name: str) -> Optional[tuple]:
        """解析工具名称，返回 (真实名, 函数) 或 None"""
        real_name = self._aliases.get(name, name)
        tool = self._tools.get(real_name)
        if tool:
            return real_name, self._funcs.get(real_name)
        return None

    def _extract_params(self, func: Callable) -> List[ToolParam]:
        """从函数 signature 自动提取参数 schema"""
        params = []
        try:
            sig = inspect.signature(func)
            for param_name, param in sig.parameters.items():
                if param_name in ("self", "cls"):
                    continue
                default = param.default
                if default is inspect.Parameter.empty:
                    default_val = None
                    required = True
                else:
                    default_val = str(default) if not isinstance(default, type) else None
                    required = False

                # 推断类型
                py_type = param.annotation if param.annotation != inspect.Parameter.empty else str
                ptype = self._python_type_to_json(py_type)

                params.append(ToolParam(
                    name=param_name,
                    description=f"参数 {param_name}",
                    type=ptype,
                    default=default_val,
                ))
        except (ValueError, TypeError):
            pass
        return params

    def _python_type_to_json(self, py_type) -> str:
        """Python 类型 → JSON 类型"""
        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }
        if py_type in type_map:
            return type_map[py_type]
        if hasattr(py_type, "__origin__"):  # Generic types like List[str]
            origin = getattr(py_type, "__origin__", None)
            if origin is list:
                return "array"
            if origin is dict:
                return "object"
        return "string"

    def _func_doc(self, func: Callable) -> str:
        """获取函数的文档字符串第一行"""
        doc = inspect.getdoc(func) or ""
        return doc.split("\n")[0] if doc else ""


# === 全局工具注册表实例 ===

TOOL_REGISTRY = ToolRegistry()


# === 内置工具实现 ===

def shell_tool(command: str, timeout: int = 30) -> str:
    """
    Shell 命令执行工具

    Args:
        command: 要执行的 shell 命令
        timeout: 超时秒数（默认 30s）
    """
    import subprocess
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout or "（命令执行成功，无输出）"
        else:
            return f"❌ 命令失败 (code {result.returncode}):\n{result.stderr}"
    except subprocess.TimeoutExpired:
        return f"⏰ 命令超时（>{timeout}s）"
    except Exception as e:
        return f"❌ 执行异常: {e}"


def file_read_tool(path: str, limit: int = 500) -> str:
    """
    文件读取工具

    Args:
        path: 文件绝对路径
        limit: 最大读取行数
    """
    import os
    try:
        if not os.path.exists(path):
            return f"❌ 文件不存在: {path}"
        with open(path, "r", encoding="utf-8") as f:
            lines = [f.readline() for _ in range(limit)]
            content = "".join(lines)
            if len(content) > 50_000:
                return content[:50_000] + "\n...（文件过长已截断）"
            return content
    except Exception as e:
        return f"❌ 读取失败: {e}"


def file_write_tool(path: str, content: str) -> str:
    """
    文件写入工具

    Args:
        path: 文件绝对路径
        content: 要写入的内容
    """
    import os
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"✅ 写入成功: {path} ({len(content)} chars)"
    except Exception as e:
        return f"❌ 写入失败: {e}"


# === 升级系统工具 ===

def _make_upgrade_tool():
    """延迟导入，避免循环依赖。"""
    import sys
    from pathlib import Path

    # 确保 hongjun_upgrader 在 path 中
    src_root = Path("/home/asus/hongjun/src")
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))

    from hongjun_upgrader import HongjunUpgrader
    return HongjunUpgrader


def upgrade_status_tool() -> str:
    """
    查询鸿钧当前版本和升级系统状态。
    无需参数。
    """
    try:
        U = _make_upgrade_tool()()
        s = U.status()
        lines = [
            f"📦 当前版本：{s['current_version']}",
            f"🔒 受保护目录：{', '.join(s['protected_zones'].keys())}",
            f"📁 可升级目录：{', '.join(s['upgradable_dirs'])}",
        ]
        if s.get("last_upgrade"):
            lu = s["last_upgrade"]
            lines.append(f"🕐 上次升级：{lu.get('time','')} [{lu.get('action','')}] {lu.get('detail','')}")
        backups = s.get("available_backups", [])
        if backups:
            lines.append(f"💾 可用备份：{len(backups)} 个")
        else:
            lines.append("💾 无备份")
        if s.get("changelog"):
            lines.append(f"\n📝 升级日志：\n{s['changelog'][:200]}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 查询失败: {e}"


def upgrade_run_tool(
    target_version: str = "",
    changelog: str = "",
    bump: str = "patch",
    dry_run: bool = False,
) -> str:
    """
    执行鸿钧升级（自动备份，失败自动回滚）。

    Args:
        target_version: 目标版本号（如 "0.3.0"），与 bump 二选一
        changelog: 本次升级的变更说明
        bump: 升级级别（major/minor/patch），默认 patch
        dry_run: True 则只检查不执行
    """
    try:
        U = _make_upgrade_tool()()
        current = U.get_current_version()
        lines = [f"🔄 开始升级（当前版本: {current}）"]

        if dry_run:
            lines.append("🧪 Dry run 模式（不实际执行）")

        # 参数处理
        bump_arg = bump if bump in ("major", "minor", "patch") else "patch"
        target_arg = target_version if target_version else None

        result = U.upgrade(
            target_version=target_arg,
            changelog=changelog,
            bump=bump_arg,
            dry_run=dry_run,
        )

        if result["success"]:
            lines.append(f"✅ 升级成功: v{current} -> v{result['version']} ({result['level']})")
            if result.get("backup"):
                lines.append(f"💾 备份位置: {result['backup']}")
            if result.get("detail"):
                lines.append(f"📝 {result['detail']}")
        else:
            lines.append(f"❌ 升级失败: {result.get('detail', '未知错误')}")
            if result.get("result") == "rolled_back":
                lines.append("↩️ 已自动回滚到备份版本")

        return "\n".join(lines)
    except Exception as e:
        return f"❌ 升级异常: {e}"


def upgrade_repair_tool() -> str:
    """
    修复鸿钧：检查核心文件完整性，尝试从备份恢复，重启服务。
    无需参数。
    """
    try:
        U = _make_upgrade_tool()()
        result = U.repair()
        lines = [f"🔧 修复完成: {result['detail']}"]
        if result.get("repaired"):
            lines.append(f"🔄 已修复: {', '.join(result['repaired'])}")
        if result.get("failed"):
            lines.append(f"❌ 修复失败: {', '.join(result['failed'])}")
        lines.append(f"🚀 服务重启: {'成功' if result.get('services_restarted') else '失败'}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 修复异常: {e}"


def upgrade_rollback_tool(backup_path: str = "") -> str:
    """
    回滚鸿钧到上一个备份版本。

    Args:
        backup_path: 可选，指定备份文件路径；默认使用最新备份
    """
    try:
        U = _make_upgrade_tool()()
        bp = Path(backup_path) if backup_path else None
        result = U.rollback(backup_path=bp)
        if result["success"]:
            return f"↩️ 回滚成功: {result['detail']}（版本: {result.get('version', '未知')}）"
        else:
            return f"❌ 回滚失败: {result.get('detail', '未知错误')}"
    except Exception as e:
        return f"❌ 回滚异常: {e}"


# === Hindsight 记忆系统 ===
_hindsight_integration = None


def _get_hindsight_integration():
    global _hindsight_integration
    if _hindsight_integration is None:
        from hongjun.hindsight_integration import HindsightIntegration
        _hindsight_integration = HindsightIntegration()
    return _hindsight_integration


def hindsight_retain_tool(
    content: str,
    context: str = None,
    memory_type: str = None,
    tags: list = None,
) -> str:
    """
    存入 Hindsight 长期记忆（retain）。

    Hindsight 自动完成：
      - 实体/关系/时间信息提取
      - 分类到四网络之一（world/experience/opinion/observation）
      - 多策略索引（向量 + BM25 + 图）
    """
    hi = _get_hindsight_integration()
    r = hi.retain(content=content, context=context, memory_type=memory_type, tags=tags or [])
    if r["success"]:
        return f"✅ 已存入 Hindsight 记忆: {content[:60]}..."
    return f"⚠️ Hindsight 不可用（{r['error']}）：{content[:60]}..."


def hindsight_recall_tool(query: str, budget: str = "mid", max_results: int = 10) -> str:
    """
    检索 Hindsight 记忆（recall）。

    多策略并行检索：
      1. 语义向量搜索
      2. BM25 关键词搜索
      3. 实体图遍历
      4. 时间过滤

    Args:
        query: 搜索 query
        budget: 'low'/'mid'/'high'，越高越全面
        max_results: 最大返回数
    """
    hi = _get_hindsight_integration()
    r = hi.recall(query=query, budget=budget, max_results=max_results)
    if not r["success"]:
        return f"⚠️ Hindsight 不可用（{r['error']}）"
    if not r["results"]:
        return "🔍 Hindsight 中没有找到相关记忆。"
    lines = [f"📚 找到 {len(r['results'])} 条相关记忆："]
    for m in r["results"][:5]:
        mem_type = f"[{m.get('memory_type', '?')}]" if m.get("memory_type") else ""
        lines.append(f"  {mem_type} {m['text'][:120]}")
    return "\n".join(lines)


def hindsight_reflect_tool(query: str) -> str:
    """
    深度反思（reflect）——综合所有记忆进行推理。

    不同于 recall 返回原始片段，reflect 会对记忆库进行深度推理，
    产生有洞察的分析和结论。可以形成和更新信念（beliefs）。
    """
    hi = _get_hindsight_integration()
    r = hi.reflect(query=query)
    if r["success"]:
        return r["text"]
    return f"⚠️ Hindsight 不可用（{r['error']}）"


# === 注册内置工具 ===

TOOL_REGISTRY.register(
    name="shell",
    func=shell_tool,
    description="执行 Shell 命令（ls/cat/grep/python 等）",
    parameters=[
        ToolParam(name="command", description="要执行的 shell 命令", type="string"),
        ToolParam(name="timeout", description="超时秒数（默认30s）", type="integer", default="30"),
    ],
)

TOOL_REGISTRY.register(
    name="file_read",
    func=file_read_tool,
    description="读取文件内容",
    parameters=[
        ToolParam(name="path", description="文件绝对路径", type="string"),
        ToolParam(name="limit", description="最大读取行数（默认500）", type="integer", default="500"),
    ],
)

TOOL_REGISTRY.register(
    name="file_write",
    func=file_write_tool,
    description="写入文件内容",
    parameters=[
        ToolParam(name="path", description="文件绝对路径", type="string"),
        ToolParam(name="content", description="要写入的内容", type="string"),
    ],
)

# 注册升级系统工具
TOOL_REGISTRY.register(
    name="upgrade_status",
    func=upgrade_status_tool,
    description="查询鸿钧当前版本和升级系统状态（版本/备份/升级历史）",
    parameters=[],
    category="system",
)

TOOL_REGISTRY.register(
    name="upgrade_run",
    func=upgrade_run_tool,
    description="执行鸿钧升级（自动备份，失败自动回滚）；支持指定版本号或 bump 级别",
    parameters=[
        ToolParam(name="target_version", description="目标版本号（如 0.3.0），与 bump 二选一", type="string", default=""),
        ToolParam(name="changelog", description="本次升级的变更说明", type="string", default=""),
        ToolParam(name="bump", description="升级级别 major/minor/patch（默认 patch）", type="string", default="patch"),
        ToolParam(name="dry_run", description="True 则只检查不执行", type="boolean", default="False"),
    ],
    category="system",
)

TOOL_REGISTRY.register(
    name="upgrade_repair",
    func=upgrade_repair_tool,
    description="修复鸿钧：检查核心文件完整性，尝试从备份恢复缺失/损坏文件，重启服务",
    parameters=[],
    category="system",
)

TOOL_REGISTRY.register(
    name="upgrade_rollback",
    func=upgrade_rollback_tool,
    description="回滚鸿钧到上一个备份版本（默认使用最新备份）",
    parameters=[
        ToolParam(name="backup_path", description="可选，指定备份文件完整路径", type="string", default=""),
    ],
    category="system",
)

# === Hindsight 记忆系统 ===
TOOL_REGISTRY.register(
    name="hindsight_retain",
    func=hindsight_retain_tool,
    description="存入 Hindsight 长期记忆（自动实体/关系提取 + 四网络分类）",
    parameters=[
        ToolParam(name="content", description="要记忆的内容（自然语言）", type="string"),
        ToolParam(name="context", description="简短标签：user_preference / project_decision / error_fix", type="string", default=""),
        ToolParam(name="memory_type", description="world/experience/opinion/observation（不提供则自动判断）", type="string", default=""),
        ToolParam(name="tags", description="自定义标签列表", type="array", default="[]"),
    ],
    category="memory",
)

TOOL_REGISTRY.register(
    name="hindsight_recall",
    func=hindsight_recall_tool,
    description="多策略检索 Hindsight 记忆（向量 + BM25 + 图遍历 + 时间过滤）",
    parameters=[
        ToolParam(name="query", description="搜索 query", type="string"),
        ToolParam(name="budget", description="检索深度 low/mid/high（默认 mid）", type="string", default="mid"),
        ToolParam(name="max_results", description="最大返回数（默认 10）", type="integer", default="10"),
    ],
    category="memory",
)

TOOL_REGISTRY.register(
    name="hindsight_reflect",
    func=hindsight_reflect_tool,
    description="深度反思 Hindsight 记忆库（综合推理，形成分析结论，可更新信念）",
    parameters=[
        ToolParam(name="query", description="反思问题", type="string"),
    ],
    category="memory",
)


# === Skills 外接系统初始化 ===

def init_skills() -> None:
    """
    初始化 Skills 外接系统。

    发现并加载 hongjun/skills/ 下的所有 skill，
    将其函数注册到 TOOL_REGISTRY。
    """
    from .skill_manager import SKILL_MANAGER

    skills = SKILL_MANAGER.discover()

    # 注册 skill 函数到 TOOL_REGISTRY
    for skill in skills:
        for func_name, func in skill.functions.items():
            tool_name = f"skill_{skill.name}__{func_name}"
            TOOL_REGISTRY.register(
                name=tool_name,
                func=func,
                description=f"[{skill.category}] {skill.description[:100]}",
                category=skill.category,
            )
            logger.info("skill_tool_registered", tool=tool_name)

    logger.info("skills_system_initialized", skill_count=len(skills))


# 启动时自动初始化 skills（延迟，Gateway 启动时调用）
# 注意：暂时注释掉，避免启动时自动加载（由 gateway 显式调用）
# init_skills()
