"""
工部 · 执行引擎
=================

代码生成 + 执行 + 文件操作的编排中心。

基于 LangGraph 的 ReAct 模式：
  Thought → Action → Observation → ...


三层执行模式：
  1. 直接执行：简单命令直接运行
  2. 工具执行：通过 ToolNode 调用工具
  3. Agent 执行：复杂任务走完整 ReAct 循环

用法：
  executor = HongjunExecutor()
  result = executor.execute("写一个 quicksort 并保存到 /tmp/sort.py")
"""

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from typing import Dict, Any, Optional, List
import subprocess
import os


# === 内置工具（LangChain @tool 装饰器）===

@tool
def shell_execute(command: str, timeout: int = 30) -> str:
    """
    执行 Shell 命令。

    适用于：ls/cat/grep/python/git/curl 等标准命令。

    Args:
        command: 要执行的命令（完整字符串）
        timeout: 超时秒数，默认 30s

    Returns:
        命令 stdout（截断至 10k 字符）
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=min(timeout, 120),  # 最多 120s
        )
        output = result.stdout or "(无 stdout)"
        if len(output) > 10_000:
            output = output[:10_000] + f"\n... (输出截断，共 {len(result.stdout)} 字符)"
        if result.returncode != 0:
            return f"⚠️ 命令退出码 {result.returncode}:\n{result.stderr or output}"
        return output
    except subprocess.TimeoutExpired:
        return f"⏰ 命令超时（>{timeout}s）"
    except Exception as e:
        return f"❌ 执行异常: {e}"


@tool
def read_file(path: str, offset: int = 1, limit: int = 200) -> str:
    """
    读取文件内容。

    适用于：查看代码/配置/日志等文本文件。

    Args:
        path: 文件绝对路径
        offset: 起始行号（1-indexed）
        limit: 最大读取行数

    Returns:
        文件内容（带行号）
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        start = max(0, offset - 1)
        end = start + limit
        selected = lines[start:end]

        content = "".join(selected)
        total = len(lines)

        header = f"=== {path} (行 {offset}-{end}，共 {total} 行) ===\n"
        return header + content + (f"\n... (共 {total} 行，已截断)" if total > end else "")
    except FileNotFoundError:
        return f"❌ 文件不存在: {path}"
    except Exception as e:
        return f"❌ 读取失败: {e}"


@tool
def write_file(path: str, content: str) -> str:
    """
    写入文件内容（覆盖）。

    适用于：创建/更新代码文件/配置/脚本。

    Args:
        path: 文件绝对路径
        content: 要写入的内容

    Returns:
        成功/失败消息
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"✅ 写入成功: {path} ({len(content)} 字符)"
    except Exception as e:
        return f"❌ 写入失败: {e}"


@tool
def list_directory(path: str = ".") -> str:
    """
    列出目录内容。

    适用于：查看文件夹结构/找文件/确认路径存在。

    Args:
        path: 目录路径（默认当前目录）
    """
    try:
        entries = os.listdir(path)
        formatted = []
        for e in sorted(entries):
            full = os.path.join(path, e)
            if os.path.isdir(full):
                formatted.append(f"  📁 {e}/")
            else:
                size = os.path.getsize(full)
                formatted.append(f"  📄 {e} ({size:,} bytes)")
        return "\n".join(formatted) or "(空目录)"
    except FileNotFoundError:
        return f"❌ 目录不存在: {path}"
    except Exception as e:
        return f"❌ 列出失败: {e}"


# === 工具列表 ===
HONGJUN_DEV_TOOLS = [
    shell_execute,
    read_file,
    write_file,
    list_directory,
]


class HongjunExecutor:
    """
    鸿钧执行引擎（工部尚书）

    提供三种执行模式：
      execute_simple()  — 简单命令直执行
      execute_tools()   — 工具调用
      execute_agent()   — 完整 ReAct Agent
    """

    def __init__(
        self,
        model_name: str = "gpt-4o",
        api_key: Optional[str] = None,
    ):
        self.model_name = model_name
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._agent = None

    def _get_agent(self):
        """懒加载 ReAct Agent"""
        if self._agent is None:
            llm = ChatOpenAI(
                model=self.model_name,
                api_key=self.api_key,
                temperature=0.3,
            )
            self._agent = create_react_agent(
                model=llm,
                tools=HONGJUN_DEV_TOOLS,
            )
        return self._agent

    def execute_simple(self, command: str) -> str:
        """
        简单命令执行（无需 LLM）

        适用于：ls/cat/grep/python 等标准命令。
        """
        return shell_execute.invoke({"command": command})

    def execute_tools(self, task: str, **kwargs) -> str:
        """
        工具调用（简单任务，不需要完整 Agent）

        适用于：读写文件、查目录等单步操作。
        """
        # 解析任务意图，选择工具
        if "目录" in task or "文件夹" in task or "list" in task.lower():
            path = kwargs.get("path", ".")
            return list_directory.invoke({"path": path})

        if "读取" in task or "查看" in task or "cat " in task:
            path = kwargs.get("path", "")
            return read_file.invoke({"path": path})

        if "写入" in task or "写" in task or "创建" in task:
            path = kwargs.get("path", "")
            content = kwargs.get("content", "")
            return write_file.invoke({"path": path, "content": content})

        if "执行" in task or "运行" in task or "python" in task.lower():
            command = kwargs.get("command", task)
            return shell_execute.invoke({"command": command})

        return f"⚠️ 无法解析任务: {task}"

    def execute_agent(self, task: str, **kwargs) -> Dict[str, Any]:
        """
        完整 ReAct Agent 执行（复杂任务）

        适用于：需要多步推理 + 工具调用的复杂任务。
        例如："帮我把这个目录里的所有 py 文件加类型注解"

        Returns:
            {"response": str, "tool_calls": list}
        """
        agent = self._get_agent()

        messages = [{"role": "user", "content": task}]
        response = agent.invoke({"messages": messages})

        # 提取结果
        final_message = response.get("messages", [])[-1]
        return {
            "response": final_message.content if hasattr(final_message, "content") else str(final_message),
            "messages": response.get("messages", []),
        }

    def execute(self, task: str) -> str:
        """
        智能执行入口

        自动选择执行模式：
          简单命令 → execute_simple
          单步工具 → execute_tools
          复杂推理 → execute_agent
        """
        # 判断任务复杂度
        simple_patterns = ["ls", "cat ", "grep ", "echo ", "pwd", "whoami", "git "]
        if any(task.strip().startswith(p) or task.strip().startswith(p.rstrip())
               for p in ["ls", "cat", "grep", "echo", "pwd", "whoami"]):
            return self.execute_simple(task)

        # 带文件路径的简单读写
        if task.startswith(("cat ", "ls ", "cd ")):
            return self.execute_simple(task)

        # 默认走 Agent
        result = self.execute_agent(task)
        return result["response"]


# === 单元测试 ===
if __name__ == "__main__":
    executor = HongjunExecutor()

    print("=" * 50)
    print("鸿钧 · 工部执行引擎测试")
    print("=" * 50)

    # 测试 1：简单命令
    print("\n📂 测试1：目录列表")
    print(executor.execute_simple("ls /home/asus/hongjun/src/"))

    # 测试 2：工具调用
    print("\n📝 测试2：读取文件")
    print(executor.execute_tools("读取文件", path="/home/asus/hongjun/SPEC.md", limit=5))

    # 测试 3：复杂任务（需要 LLM）
    print("\n🤖 测试3：智能执行（需要 API key）")
    if executor.api_key:
        result = executor.execute_agent("列出 /home/asus 目录下的文件夹")
        print(result["response"][:500])
    else:
        print("⚠️ 未配置 OPENAI_API_KEY，跳过 LLM 测试")
