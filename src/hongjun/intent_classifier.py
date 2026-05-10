"""
意图分类器 — LLM-based Few-Shot Intent Classification
=====================================================

用途：在 dispatch 前先做意图分类，决定是否进入 local-executor 分支，
      避免关键词匹配导致的误触发。

分类体系（8类）：
  code_generation  — 写代码/开发/实现算法
  search           — 搜索/查询/查找信息
  memory_recall    — 记忆检索/之前/上次
  git_operation    — git commit/push/branch/PR
  shell_command    — 执行shell命令/terminal/ps/cat/grep
  system_status    — 系统状态/模块展示/健康检查
  file_operation   — 读文件/写文件/查看文件
  deploy           — 部署/运行脚本
  unclear          — 无法判断 → 不执行 shell，返回"请澄清"

工作原理：
  Few-shot classification：每次分类携带 2-3 个示例，
  LLM 根据示例理解每个类别的语义边界，而非关键词匹配。
"""

import json
import re
from typing import Optional

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.intent_classifier")

# ── Intent Categories ────────────────────────────────────────────────────────

INTENT_CATEGORIES = [
    {
        "name": "code_generation",
        "description": "用户要求写代码、开发程序、实现算法或函数、生成代码片段",
        "examples": [
            "帮我写一个快速排序",
            "用 Python 实现一个链表",
            "写个读取 CSV 文件的脚本"
        ]
    },
    {
        "name": "search",
        "description": "用户要求搜索信息、查询资料、查找内容、了解趋势",
        "examples": [
            "搜索一下最新的 AI 新闻",
            "查一下 Python 趋势",
            "找找关于 React 的教程"
        ]
    },
    {
        "name": "memory_recall",
        "description": "用户要求回忆之前讨论过的内容、查询记忆",
        "examples": [
            "我们之前讨论过什么",
            "回忆一下上次说的项目",
            "之前我让你做的需求是什么"
        ]
    },
    {
        "name": "git_operation",
        "description": "用户要求执行 Git 操作：commit、push、branch、checkout、PR、merge",
        "examples": [
            "提交代码到仓库",
            "推送到远程分支",
            "创建一个新分支"
        ]
    },
    {
        "name": "shell_command",
        "description": "用户要求执行 shell 命令、查看系统状态、进程管理、文件操作等日常命令",
        "examples": [
            "运行 ls -la",
            "查看进程列表",
            "杀掉某个进程",
            "检查服务状态"
        ]
    },
    {
        "name": "system_status",
        "description": "用户要求查看系统模块状态、健康检查、运行状态、内存/CPU/负载",
        "examples": [
            "展示你的各项系统模块",
            "检查系统状态",
            "查看运行状态",
            "系统健康检查",
            "查看内存使用情况",
            "查看系统内存",
            "查看CPU使用",
            "查看负载"
        ]
    },
    {
        "name": "file_operation",
        "description": "用户要求读取、写入、查看文件内容",
        "examples": [
            "查看 /etc/hostname",
            "读取 config.json",
            "把内容写入 test.txt"
        ]
    },
    {
        "name": "deploy",
        "description": "用户要求部署、运行部署脚本",
        "examples": [
            "运行部署脚本",
            "执行 deploy.sh",
            "部署到服务器"
        ]
    },
    {
        "name": "service_management",
        "description": "用户要求启动、停止、重启、查看服务状态",
        "examples": [
            "启动 nginx 服务",
            "停止 cron 服务",
            "重启 docker 服务",
            "查看服务状态"
        ]
    },
    {
        "name": "config_edit",
        "description": "用户要求修改配置文件或环境变量",
        "examples": [
            "修改 nginx 配置",
            "更改环境变量",
            "编辑 /etc/hosts",
            "把 DEBUG 改成 true"
        ]
    },
    {
        "name": "upgrade",
        "description": "用户要求升级/更新/修复鸿钧系统本身，或查看版本/升级状态",
        "examples": [
            "升级鸿钧",
            "升级到最新版本",
            "查看当前版本",
            "修复鸿钧",
            "回滚到上一版本",
            "升级系统状态"
        ]
    },
]

# 简单指令（不需要 LLM，可直接放行）
SIMPLE_SHELL_PATTERNS = [
    re.compile(r"^(ls|ps|cd|cat|grep|pwd|whoami|df|free|top|uname|hostname|uptime|curl|wget)(?:\s|$)"),
    re.compile(r"^git\s+(status|log|diff|branch|checkout|commit|push|pull|merge|fetch|clone)(?:\s|$)"),
    re.compile(r"^systemctl\s+(status|start|stop|restart|enable|disable)\s+"),
    re.compile(r"^/[\w/.-]+\.(sh|py|js|ts|go|rs|cpp|c|h)$"),
    re.compile(r"^[\"'].+?[\"']$"),  # quoted command: "ls -la"
]

SIMPLE_SHELL_KEYWORDS = [
    "ls ", "ps ", "cd ", "cat ", "grep ", "pwd", "df -h", "free -h",
    "top", "uname", "hostname", "uptime", "curl ", "wget ",
    "git status", "git log", "git diff", "git branch", "git checkout",
    "systemctl status", "systemctl restart", "systemctl stop",
    "ps aux", "kill ", "pkill",
]


def _build_few_shot_prompt(request: str) -> str:
    """构建 few-shot classification prompt"""
    examples_lines = []
    for cat in INTENT_CATEGORIES:
        for ex in cat["examples"][:2]:
            examples_lines.append(f'  - "{ex}" → {cat["name"]}')
    examples_text = "\n".join(examples_lines)

    categories_text = "\n".join(
        f'  - {c["name"]}: {c["description"]}'
        for c in INTENT_CATEGORIES
    )

    return f"""你是一个意图分类器。根据用户消息判断其意图类别。

## 候选类别
{categories_text}

## 示例（格式：消息 → 类别）
{examples_text}

## 待分类消息
"{request}"

## 输出格式（只输出 JSON）
{{"intent": "类别名", "confidence": 0.0-1.0}}
"""


def _parse_llm_response(text: str) -> Optional[dict]:
    """从 LLM 输出中解析 intent + confidence

    LLM 响应格式（MiniMax-M2.7）：
      <think>...思考内容...```
      {"intent": "...", "confidence": 0.95}
      ```
    1. 先剥离 <think>... 标签
    2. 提取 ```json ... ``` 中的 JSON
    3. 若找不到 JSON 块，从最后一个 { 开始尝试解析
    """
    text = text.strip()

    # 1. 剥离 <think>... 思考标签
    text = re.sub(r'<think>[\s\S]*?</think>', '', text)

    # 2. 提取 ```json ... ``` 中的 JSON
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.DOTALL)
    if m:
        json_str = m.group(1)
    else:
        # 3. 从最后一个 { 开始尝试解析
        last_brace = text.rfind('{')
        json_str = text[last_brace:] if last_brace >= 0 else text

    try:
        data = json.loads(json_str)
        intent = str(data.get("intent", "unclear"))
        confidence = float(data.get("confidence", 0.5))
        return {"intent": intent, "confidence": confidence}
    except Exception:
        return None


def classify_intent(request: str) -> dict:
    """
    对用户请求进行意图分类。

    Returns:
        {
            "intent": str,          # 类别名
            "confidence": float,     # 置信度 0.0-1.0
            "is_shell_safe": bool,   # 是否适合走 shell 命令执行
        }
    """
    # 0. 极短指令走快速路径（避免 LLM 开销）
    req = request.strip()
    if len(req) <= 3:
        return {"intent": "unclear", "confidence": 0.0, "is_shell_safe": False}

    # 1. 简单命令模式匹配
    for pattern in SIMPLE_SHELL_PATTERNS:
        if pattern.match(req):
            return {"intent": "shell_command", "confidence": 0.95, "is_shell_safe": True}

    # 2. 简单关键词匹配
    req_lower = req.lower()
    for kw in SIMPLE_SHELL_KEYWORDS:
        if req_lower.startswith(kw) or f" {kw}" in req_lower:
            return {"intent": "shell_command", "confidence": 0.85, "is_shell_safe": True}

    # 3. LLM few-shot 分类
    try:
        from .gateway.server import _get_llm
        llm = _get_llm()
        if llm is None:
            return {"intent": "unclear", "confidence": 0.0, "is_shell_safe": False}

        prompt = _build_few_shot_prompt(req)
        response = llm.chat_sync([{"role": "user", "content": prompt}])

        result = _parse_llm_response(
            response.content if hasattr(response, "content") else str(response)
        )
        if result:
            is_shell_safe = result["intent"] in {
                "shell_command", "git_operation", "file_operation",
                "system_status", "deploy"
            }
            return {
                "intent": result["intent"],
                "confidence": result["confidence"],
                "is_shell_safe": is_shell_safe,
            }
    except Exception as e:
        logger.warning("llm_classification_failed", error=str(e))

    return {"intent": "unclear", "confidence": 0.0, "is_shell_safe": False}


def intent_to_handler_key(intent: str) -> str:
    """
    将 intent 映射到 orchestrator handler 的 elif 分支关键字。
    返回的 key 用于 dispatch_and_execute 中的分支匹配。
    """
    mapping = {
        "git_operation":     "git_operation",
        "shell_command":     "shell_command",
        "system_status":    "system_status",
        "file_operation":    "file_operation",
        "deploy":            "deploy",
        "code_generation":   "code_generation",
        "search":           "search",
        "memory_recall":    "memory_recall",
        "service_management": "service_management",
        "systemctl":        "systemctl",
        "config_edit":      "config_edit",
        "upgrade":          "upgrade",
    }
    return mapping.get(intent, "unclear")
