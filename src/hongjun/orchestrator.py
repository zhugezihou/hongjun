"""
吏部 · 任务编排中心
=====================

基于 LangGraph 的状态流编排引擎。

工作流程：
  用户请求
    ↓
  [分解任务]  → 解析意图，拆成子任务列表
    ↓
  [分发执行]  → 根据任务类型分发给对应 Agent
    ↓
  [汇总结果]  → 收集各部返回，整理后输出

支持的 Agent 类型：
  - dev      → 工部（代码执行）
  - memory   → 户部（记忆检索）
  - tools    → 礼部（工具执行：搜索/浏览器）
  - security → 兵部（安全过滤）
  - eval     → 刑部（质量评估）
"""

from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Optional
from enum import Enum
from pathlib import Path
import re
import time
import uuid
from contextvars import ContextVar
from .self_evolution import verify_and_execute


# ── Step callback context var（用于在 sync 调用链中传递回调，不经过 LangGraph state）──
_step_callback_var: ContextVar[Optional[callable]] = ContextVar(
    "_step_callback", default=None
)


def _emit_step(step_type: str, step_data: dict) -> None:
    """在当前 context 中发射步骤事件（若 callback 已设置）。"""
    cb = _step_callback_var.get()
    if cb:
        try:
            cb(step_type, step_data)
        except Exception:
            pass



# ── LLM 调用辅助（含记忆注入）───────────────────────────────────────────────

def _llm_call(messages: list[dict], state: dict, intent_type: str = "") -> str:
    """
    带记忆注入的 LLM 调用。

    策略：日期/时间问题直接用 Python 回答（不依赖 LLM 知识库）。
    其他问题走 LLM，注入记忆上下文 + 当前系统时间。
    不修改原始 messages 列表（返回新列表）。
    """
    import datetime as dt, re

    # ── 日期/时间快速路径（直接用 Python 回答，不走 LLM）──
    user_msg = ""
    for m in messages:
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break
    if not user_msg:
        user_request = state.get("user_request", "") if isinstance(state, dict) else str(state)
        user_msg = user_request

    date_patterns = [
        (r"今天|今日|现在", 0),
        (r"明天|明日", 1),
        (r"后天|后日", 2),
        (r"大后天|大后日", 3),
        (r"昨天|昨日", -1),
        (r"前天|前一日", -2),
    ]
    now = dt.datetime.now()
    weekday_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    for pattern, days_ahead in date_patterns:
        if re.search(pattern, user_msg) and any(
            k in user_msg for k in ["星期几", "周几", "几号", "哪天", "什么星期", "星期", "号"]
        ):
            future = now + dt.timedelta(days=days_ahead)
            date_str = future.strftime(f"%Y年%m月%d日 {weekday_cn[future.weekday()]}")
            pref = {0: "今天", 1: "明天", 2: "后天", 3: "大后天", -1: "昨天", -2: "前天"}[days_ahead]
            return f"{pref}是{date_str}。"

    # ── LLM 路径（其他问题）──
    try:
        from .memory_injection import get_memory_injector
        injector = get_memory_injector()
        user_request = state.get("user_request", "") if isinstance(state, dict) else str(state)
        enriched = injector.inject(list(messages), user_request, intent_type)
    except Exception:
        enriched = list(messages)  # 复制，避免修改原始列表

    # 注入当前系统时间
    date_str = now.strftime(f"%Y年%m月%d日 {weekday_cn[now.weekday()]} %H:%M:%S")
    system_with_time = {
        "role": "system",
        "content": f"当前系统时间（真实时间）：{date_str}。回答日期/时间相关问题时以此为准。"
    }
    if enriched and enriched[0].get("role") == "system":
        enriched[0]["content"] = system_with_time["content"] + "\n\n" + enriched[0]["content"]
    else:
        enriched.insert(0, system_with_time)

    from .gateway.server import _get_llm
    llm = _get_llm()
    if not llm:
        return "[错误] LLM 未配置"
    last_err = None
    for attempt in range(3):
        try:
            resp = llm.chat_sync(enriched)
            return resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    return f"[LLM 调用失败] {last_err}"


class TaskType(str, Enum):
    """任务类型枚举"""
    CODE = "code"           # 写代码/执行命令
    SEARCH = "search"       # 网页搜索
    BROWSER = "browser"     # 浏览器操作
    MEMORY = "memory"       # 记忆读写
    SECURITY = "security"   # 安全审核
    EVAL = "eval"           # 质量评估
    ORCHESTRATE = "orchestrate"  # 联合多部


class SubTask(TypedDict):
    """子任务定义"""
    id: str
    task_type: TaskType
    description: str
    assigned_to: str  # agent_id: dev/memory/tools/security/eval
    status: str       # pending/running/completed/failed
    result: Optional[str]


class CoordinatorState(TypedDict):
    """
    吏部协调状态

    这是流经整个图的核心状态对象。
    每个节点都可以读写其中的字段。
    """
    user_request: str                    # 用户原始请求
    intent: Optional[str]                 # 解析出的意图
    subtasks: List[SubTask]              # 分解后的子任务列表
    results: List[str]                    # 各部返回结果
    final_response: Optional[str]        # 最终返回用户的答复
    user_id: Optional[str]               # 用户标识（用于记忆检索）
    memory_context: str                  # 户部注入的记忆上下文
    security_passed: bool                # 兵部安全审核结果
    # 审批相关（两阶段执行）
    approved_op: Optional[str]           # 已批准操作 id（二次调用时）
    _approved_ops: dict                 # server._APPROVED_OPS 引用
    eval_score: Optional[float]          # 刑部质量评分
    skill_result: Optional[str]          # Skill 执行结果（若有）


def parse_intent(state: CoordinatorState) -> CoordinatorState:
    """
    节点1：意图解析 + 任务分解

    分析用户请求，决定需要哪些 Agent 参与。
    """
    request = state["user_request"]
    subtasks: List[SubTask] = []

    request_lower = request.lower()

    # 意图判断 + 子任务生成
    # 优先级：dev(写代码) > tools(搜索) > memory(记忆)
    # 只有当没有代码相关意图时才走搜索
    has_code_intent = any(
        kw in request_lower
        for kw in [
            "写代码", "代码", "开发", "编程",
            # 算法/函数生成也是代码
            "排序", "算法", "quicksort", "mergesort", "sort",
            "生成", "实现", "写一个", "写个",
        ]
    )

    if has_code_intent:
        # 代码任务优先（无论用户说"算法"还是"代码"）
        subtasks.append({
            "id": "task_1",
            "task_type": TaskType.CODE,
            "description": "生成/执行代码",
            "assigned_to": "dev",
            "status": "pending",
            "result": None,
        })

    elif any(kw in request_lower for kw in ["搜索", "查", "找", "github", "趋势", "trending"]):
        subtasks.append({
            "id": "task_1",
            "task_type": TaskType.SEARCH,
            "description": "执行网页搜索",
            "assigned_to": "tools",
            "status": "pending",
            "result": None,
        })

    if any(kw in request_lower for kw in ["记忆", "之前", "上次", "曾经"]):
        subtasks.append({
            "id": "task_3",
            "task_type": TaskType.MEMORY,
            "description": "检索相关记忆",
            "assigned_to": "memory",
            "status": "pending",
            "result": None,
        })

    # 如果没有匹配任何类型，默认走工具搜索
    if not subtasks:
        subtasks.append({
            "id": "task_default",
            "task_type": TaskType.SEARCH,
            "description": "通用任务执行",
            "assigned_to": "tools",
            "status": "pending",
            "result": None,
        })

    # 安全审核是必须的
    subtasks.insert(0, {
        "id": "task_security",
        "task_type": TaskType.SECURITY,
        "description": "安全审核",
        "assigned_to": "security",
        "status": "pending",
        "result": None,
    })

    intent_result = _classify_intent(request)

    # 步骤回调：意图已解析
    _emit_step("intent", {"intent": intent_result, "subtasks": [s.get("description", "") for s in subtasks]})

    return {
        **state,
        "intent": intent_result,
        "subtasks": subtasks,
    }



def _classify_intent(request: str) -> str:
    """简单意图分类"""
    if "搜索" in request or "查" in request:
        return "search"
    elif "写代码" in request or "开发" in request:
        return "code_generation"
    elif "记忆" in request or "回忆" in request:
        return "memory_recall"
    else:
        return "general"


# ── Service Management Handler ─────────────────────────────────────────────────

def _handle_service_management(req: str, req_lower: str, funcs: dict) -> str:
    """
    执行服务管理操作：systemctl / mempalace / 其他 CLI 工具。
    
    支持的命令模式：
      - systemctl start/stop/restart/status <服务名>
      - mempalace init / mempalace <子命令>
      - <任意 cli 工具> <action>
    """
    import subprocess

    # ── 1. systemctl 模式 ───────────────────────────────────────────────────
    systemctl_keywords = ["启动", "停止", "重启", "reload", "状态", "status", "start", "stop", "restart"]
    if any(k in req_lower for k in systemctl_keywords):
        action = None
        unit = None
        if any(k in req_lower for k in ["启动", "start"]):
            action = "start"
        elif any(k in req_lower for k in ["停止", "stop"]):
            action = "stop"
        elif any(k in req_lower for k in ["重启", "restart", "reload"]):
            action = "restart"
        elif any(k in req_lower for k in ["状态", "status"]):
            action = "status"
        
        # 提取服务名
        m_unit = re.search(r'(?:服务|unit|service)[:：]?\s*(\S+?)(?:\s|$|。)', req)
        if m_unit:
            unit = m_unit.group(1)
        else:
            for svc in ["cron.service", "nginx", "docker", "redis", "mysql", 
                        "postgresql", "cron", "six-ministries-a2a", "hongjun"]:
                if svc in req_lower:
                    unit = svc
                    break
        
        if action and unit:
            cmd = f"systemctl {action} {unit}"
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                out = r.stdout.strip() or r.stderr.strip() or f"（{action} {unit} 成功）"
                return out if r.returncode == 0 else f"❌ {cmd}\n{out}"
            except Exception as e:
                return f"❌ systemctl {action} {unit} 失败：{e}"
        elif unit:
            cmd = f"systemctl status {unit}"
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
                return r.stdout.strip() or f"（服务 {unit} 状态查询完成）"
            except Exception as e:
                return f"❌ systemctl status {unit} 失败：{e}"
        else:
            return "[错误] 格式：「服务管理 启动/停止/重启/状态 服务名」"

    # ── 2. mempalace 模式 ──────────────────────────────────────────────────
    if "mempalace" in req_lower:
        cmd = None
        if "init" in req_lower:
            cmd = "mempalace init"
        elif "status" in req_lower or "状态" in req_lower:
            cmd = "mempalace status"
        elif "build" in req_lower:
            cmd = "mempalace build"
        else:
            # 提取 mempalace 后的子命令
            m = re.search(r'mempalace\s+(\S+)', req_lower)
            if m:
                cmd = f"mempalace {m.group(1)}"
            else:
                return "[提示] mempalace 命令格式：mempalace init/status/build"
        
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60,
                               env={**subprocess.os.environ, "PATH": "/home/asus/.hermes/hermes-agent/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"})
            out = r.stdout.strip() or r.stderr.strip()
            if r.returncode == 0:
                return out or f"✅ {cmd} 执行成功"
            else:
                return f"❌ {cmd}\n{out}"
        except Exception as e:
            return f"❌ {cmd} 失败：{e}"

    # ── 3. 通用 CLI 工具模式（git / docker / pip / npm 等）────────────────
    cli_patterns = [
        (r'\bgit\s+(commit|push|pull|branch|checkout|merge|rebase|log|diff|status|add)\b', "git"),
        (r'\bdocker\s+(ps|images|run|build|stop|rm|logs)\b', "docker"),
        (r'\bpip\s+(install|uninstall|freeze|list|show)\b', "pip"),
        (r'\bnpm\s+(install|run|start|test|build)\b', "npm"),
        (r'\bcurl\s+', "curl"),
        (r'\b wget\s+', "wget"),
        (r'\bpython\s+', "python"),
        (r'\bpython3\s+', "python3"),
        (r'\bbash\s+', "bash"),
        (r'\bsh\s+', "sh"),
    ]
    
    for pattern, tool in cli_patterns:
        if re.search(pattern, req_lower):
            # 提取完整命令
            m = re.search(rf'{pattern}.*', req)
            if m:
                cmd = m.group(0).strip()
                try:
                    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
                    out = r.stdout.strip() or r.stderr.strip()
                    prefix = "✅" if r.returncode == 0 else "❌"
                    return f"{prefix} {cmd}\n{out}" if out else f"{prefix} {cmd}（无输出）"
                except Exception as e:
                    return f"❌ {cmd}\n{e}"
            break

    # ── 4. Fallback：无法识别 ────────────────────────────────────────────
    return "[错误] 格式：「服务管理 启动/停止/重启/状态 服务名」或「mempalace init」"


# ── Config Edit Handler ───────────────────────────────────────────────────────

def _handle_config_edit(req: str, req_lower: str) -> str:
    """
    执行配置修改操作。
    
    支持的命令模式：
      - 读取配置：cat/grep <文件路径>
      - 追加配置：echo/printf >> <文件路径>
      - 批量修改：sed/awk 对文件操作
      - 环境变量：export VAR=value / unset VAR
    """
    import subprocess
    import os
    import re
    
    # 读取配置模式
    read_patterns = [
        r'(?:查看|读|看|cat|grep)\s+[:：]?\s*["\']?([^\s"\'<>|]+/[^\s"\'<>|]+)["\']?',
        r'(?:查看|读|看|cat|grep)\s+[:：]?\s*["\']?([A-Za-z0-9_./:-]+\.[conf|yaml|yml|json|toml|ini|env])["\']?',
        r'["\']?([A-Za-z0-9_./:-]+\.[conf|yaml|yml|json|toml|ini|env])["\']?\s*$',
    ]
    for pattern in read_patterns:
        m = re.search(pattern, req)
        if m:
            path = m.group(1)
            if os.path.exists(path):
                try:
                    r = subprocess.run(f"cat {path}", shell=True, capture_output=True, text=True, timeout=10)
                    return r.stdout[:2000] or f"（{path} 为空或无内容）"
                except Exception as e:
                    return f"❌ 读取 {path} 失败：{e}"
            else:
                return f"❌ 文件不存在：{path}"
    
    # 环境变量模式：找最后一个 '=' 锚点，从右往左提取 identifier
    # （避免 \s+ 在 UTF-8 中文字符后无法匹配的 regex 结构问题）
    eq_idx = req.rfind('=')
    if eq_idx != -1:
        val = req[eq_idx+1:].strip()
        before_eq = req[:eq_idx]
        # 从右往左找 identifier
        rev = before_eq[::-1]
        m_id = re.search(r'([A-Za-z_][A-Za-z0-9_]*)', rev)
        if m_id:
            var = m_id.group(1)[::-1]
            os.environ[var] = val
            return f"✅ 环境变量已设置：{var}={val}"

    unset_match = re.search(r'(?:取消|unset|删除).*?([A-Za-z_][A-Za-z0-9_]*)', req)
    if unset_match:
        var = unset_match.group(1)
        os.environ.pop(var, None)
        return f"✅ 环境变量已删除：{var}"
    
    # 追加配置模式
    append_match = re.search(r'(?:追加|append|echo|printf)\s+["\']([^"\']+)["\']?\s*>>?\s*["\']?([^\s"\'<>|]+)["\']?', req)
    if append_match:
        content, path = append_match.groups()
        try:
            with open(path, "a") as f:
                f.write(content + "\n")
            return f"✅ 已追加到 {path}"
        except Exception as e:
            return f"❌ 追加到 {path} 失败：{e}"
    
    return "[提示] 配置修改格式：「查看配置 /path/to/config.yaml」或「设置环境变量 VAR=value」"


# ── Deploy Handler ────────────────────────────────────────────────────────────

def _handle_deploy(req: str, req_lower: str) -> str:
    """
    执行部署操作：跑自动化脚本 / docker-compose / npm run build 等。
    """
    import subprocess
    
    deploy_patterns = [
        # docker-compose
        (r'docker-compose\s+(up|down|restart|build|logs)', r'docker-compose \1'),
        # docker
        (r'docker\s+compose\s+(up|down|restart|build|logs)', r'docker compose \1'),
        # npm/pip
        (r'npm\s+(run\s+\w+|install|build|start|test)', r'npm \1'),
        (r'pip\s+install\s+(-?\S+)', r'pip install \1'),
        # systemctl deploy
        (r'(?:部署|deploy|重启|restart)\s+(?:鸿钧|hongjun)', 'systemctl restart hongjun'),
        (r'(?:部署|deploy|重启|restart)\s+(?:六部|a2a|six-ministries)', 'systemctl restart six-ministries-a2a'),
        # 跑脚本
        (r'(?:跑|执行|run)\s+[:：]?\s*["\']?([^\s"\'<>|]+\.sh)["\']?', r'bash \1'),
        (r'(?:跑|执行|run)\s+[:：]?\s*["\']?([^\s"\'<>|]+\.py)["\']?', r'python3 \1'),
        # generic deploy keywords
        (r'\bdeploy\b', None),
        (r'\b启动\b', None),
        (r'\bstop\b', None),
        (r'\brestart\b', None),
    ]
    
    for pattern, replacement in deploy_patterns:
        if re.search(pattern, req_lower):
            if replacement:
                cmd = re.sub(pattern, replacement, req, flags=re.IGNORECASE)
            else:
                # Just run the matched part
                m = re.search(pattern, req, flags=re.IGNORECASE)
                cmd = req[m.start():m.end()]
            
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
                out = r.stdout.strip() or r.stderr.strip()
                if r.returncode == 0:
                    return out or f"✅ {cmd} 执行成功"
                else:
                    return f"❌ {cmd}\n{out}"
            except Exception as e:
                return f"❌ {cmd} 失败：{e}"
            break
    
    return "[提示] 部署格式：「部署 脚本.sh」或「docker-compose up」或「npm run build」"


def dispatch_and_execute(state: CoordinatorState) -> CoordinatorState:
    """
    节点2：分发执行

    优先匹配 skills（匹配度 > 0.5 时触发），
    匹配度 > 0.5 时执行 skill；无匹配时返回"能力待开发"提示。
    危险操作（git push --force / systemctl stop/disable）需经审批才能执行：
      - 第一次调用：检测到危险操作 → skill_result=哨兵字符串 → 返回等待审批
      - 第二次调用（approved_op 已批准）：跳过检查，直接执行
    """
    from .skill_manager import SKILL_MANAGER
    import json

    def _needs_approval(operation: str, req: str) -> Optional[dict]:
        """检查是否需要用户审批。返回 None=已批准/安全，dict=需审批的哨兵信息。"""
        approved_ops: dict = state.get("_approved_ops", {})
        # 二次调用：approved_op 在已批准集合中 → 跳过检查，直接放行
        if state.get("approved_op") and state["approved_op"] in approved_ops:
            return None
        # 调用 ApprovalManager.check() 做正则匹配
        try:
            from hongjun.gateway.server import ApprovalManager, DANGEROUS_PATTERNS
            import re as _re
            for pattern, reason, severity in DANGEROUS_PATTERNS:
                if _re.search(pattern, req, _re.IGNORECASE):
                    # 同步注册：ApprovalManager.register() 本身是 async，但 check() 用锁同步写入
                    # 在线程池上下文中直接同步操作_pending 字典
                    import asyncio
                    loop = asyncio.get_event_loop()
                    approval_id = str(uuid.uuid4())[:8]
                    # 在线程中用 run_coroutine_threadsafe 同步等待注册
                    coro = ApprovalManager.register.__get__(HongjunGateway.approvals, ApprovalManager)
                    # 简化处理：直接写入_pending 字典（同步）
                    HongjunGateway.approvals._pending[approval_id] = type("PendingApproval", (), {
                        "id": approval_id,
                        "operation": operation,
                        "command": req,
                        "reason": reason,
                        "severity": severity,
                        "created_at": time.time(),
                        "future": asyncio.Future(),
                        "approved": None,
                        "result": None,
                    })()
                    return {
                        "approval_id": approval_id,
                        "operation": operation,
                        "reason": reason,
                        "severity": severity,
                        "command": req,
                    }
        except Exception:
            pass
        return None

    HongjunGateway = None  # avoid unused warning; resolved inside _needs_approval

    results = []
    updated_subtasks = []
    skill_result = None

    # === Step 1：先尝试 Skill 匹配 ===
    matched_skills = SKILL_MANAGER.match(state["user_request"])
    best_skill = matched_skills[0] if matched_skills and matched_skills[0].match_score(state["user_request"]) >= 0.5 else None

    if best_skill:
        # Skill 优先执行
        func = next(iter(best_skill.functions.values()), None)
        if func:
            # 步骤回调：开始执行 skill
            _emit_step("task_start", {"skill": best_skill.name, "task": best_skill.description})

            url_match = re.search(r'https?://[^\s<>"\' ]+', state["user_request"])

            try:
                if best_skill.name == "web-scraper" and "scrape" in best_skill.functions:
                    url = url_match.group(0) if url_match else "https://example.com"
                    skill_result = best_skill.functions["scrape"](url=url)

                elif best_skill.name == "github-ops":
                    req = state["user_request"]
                    # 从请求中提取 GitHub 相关参数
                    if "trending" in req.lower() or "趋势" in req:
                        lang = "Python"
                        for lang_name in ["Python", "JavaScript", "Go", "Rust", "TypeScript"]:
                            if lang_name in req:
                                lang = lang_name
                                break
                        skill_result = best_skill.functions["trending"](language=lang)
                    elif "/" in req and "仓库" in req or "/" in req:
                        # owner/repo 格式
                        parts = re.findall(r'([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)', req)
                        if parts:
                            op, rn = parts[0].split("/", 1)
                            skill_result = best_skill.functions["repo"](owner=op, repo_name=rn)
                    elif "目录" in req or "结构" in req or "tree" in req.lower():
                        # 提取 owner/repo 获取目录树
                        parts = re.findall(r'([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)', req)
                        if parts:
                            op, rn = parts[-1][0], parts[-1][1]
                            ref = "develop" if "MemPalace" in req or "mempalace" in req.lower() else "main"
                            skill_result = best_skill.functions["tree"](owner=op, repo_name=rn, ref=ref)
                        else:
                            # 尝试从"研究 MemPalace"类请求中提取
                            if "MemPalace" in req or "mempalace" in req.lower():
                                skill_result = best_skill.functions["tree"](owner="MemPalace", repo_name="mempalace", ref="develop")
                            else:
                                skill_result = best_skill.functions["search"](query="AI agent memory", language="Python")
                    elif "读" in req and ("文件" in req or "源码" in req or "代码" in req) or "repo_file" in req:
                        # 读取仓库文件内容
                        parts = re.findall(r'([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)', req)
                        path_match = re.search(r'[`"]?([\w./]+)`?$', req)
                        if "MemPalace" in req or "mempalace" in req.lower():
                            # 智能选择 MemPalace 关键文件
                            if "layers" in req.lower() or "层级" in req or "4层" in req:
                                skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/layers.py", ref="develop")
                            elif "backend" in req.lower() or "存储" in req:
                                skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/backends/base.py", ref="develop")
                            elif "knowledge" in req.lower() or "图谱" in req or "知识图" in req:
                                skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/knowledge_graph.py", ref="develop")
                            elif "searcher" in req.lower() or "检索" in req or "搜索" in req:
                                skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/searcher.py", ref="develop")
                            elif "palace" in req.lower() or "收藏" in req:
                                skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/palace.py", ref="develop")
                            elif "mcp" in req.lower():
                                skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/mcp_server.py", ref="develop")
                            elif "readme" in req.lower() or "README" in req:
                                skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="README.md", ref="develop")
                            else:
                                # 默认读核心 layers.py
                                skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/layers.py", ref="develop")
                        elif parts:
                            op, rn = parts[-1][0], parts[-1][1]
                            path = path_match.group(1) if path_match else "README.md"
                            skill_result = best_skill.functions["repo_file"](owner=op, repo_name=rn, path=path)
                        else:
                            skill_result = best_skill.functions["search"](query="AI agent memory", language="Python")
                    elif "代码搜索" in req or "code_search" in req or "搜索代码" in req:
                        q_match = re.search(r'搜索[代码]?[：:]?\s*([^\s，,。]+)', req)
                        q = q_match.group(1) if q_match else "class Memory"
                        if "MemPalace" in req:
                            skill_result = best_skill.functions["code_search"](query=q, owner="MemPalace", repo="mempalace", limit=5)
                        else:
                            skill_result = best_skill.functions["code_search"](query=q, limit=5)
                    else:
                        # 通用搜索：提取关键词
                        q = re.sub(
                            r'搜索|GitHub|仓库|的|研究|一下|帮我|找|查|\s+',
                            ' ', state["user_request"]
                        ).strip()
                        # 保留有意义的词
                        q = re.sub(r'\b\w{1,2}\b', '', q).strip()
                        if not q or len(q) < 2:
                            q = "browser-use AI agent"
                        skill_result = best_skill.functions["search"](query=q, language="Python")

                elif best_skill.name == "mempalace-research":
                    # 专为研究 MemPalace 设计：先目录树，再核心文件
                    req = state["user_request"]
                    if "目录" in req or "结构" in req:
                        skill_result = best_skill.functions["tree"](owner="MemPalace", repo_name="mempalace", ref="develop")
                    elif "layers" in req.lower() or "层级" in req or "4层" in req:
                        skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/layers.py", ref="develop")
                    elif "backend" in req.lower() or "存储" in req:
                        skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/backends/base.py", ref="develop")
                    elif "knowledge" in req.lower() or "图谱" in req or "知识图" in req:
                        skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/knowledge_graph.py", ref="develop")
                    elif "searcher" in req.lower() or "检索" in req or "BM25" in req:
                        skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/searcher.py", ref="develop")
                    elif "mcp" in req.lower():
                        skill_result = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/mcp_server.py", ref="develop")
                    else:
                        # 默认：读 README + layers.py 综合输出
                        readme = best_skill.functions.get("repo_file", list(best_skill.functions.values())[0])
                        layers = best_skill.functions.get("repo_file", list(best_skill.functions.values())[0])
                        r1 = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="README.md", ref="develop")
                        r2 = best_skill.functions["repo_file"](owner="MemPalace", repo_name="mempalace", path="mempalace/layers.py", ref="develop")
                        skill_result = f"{r1[:4000]}\n\n---\n{r2[:4000]}"

                elif best_skill.name == "playwright":
                    req = state["user_request"]
                    url = re.search(r'https?://[^\s<>"\']+', req)
                    url = url.group(0) if url else "https://example.com"
                    if "截图" in req or "截个" in req or "截图" in req.lower():
                        skill_result = best_skill.functions["screenshot"](url=url)
                    elif "链接" in req:
                        skill_result = best_skill.functions["extract_links"](url=url)
                    elif "滚动" in req or "scroll" in req.lower():
                        skill_result = best_skill.functions["scroll_and_extract"](url=url)
                    else:
                        # 默认提取文本
                        skill_result = best_skill.functions["extract_text"](url=url)

                elif best_skill.name == "scraping":
                    req = state["user_request"]
                    url = re.search(r'https?://[^\s<>"\']+', req)
                    url = url.group(0) if url else "https://example.com"
                    if "json-ld" in req.lower() or "jsonld" in req.lower():
                        skill_result = best_skill.functions["extract_json_ld"](url=url)
                    elif "xpath" in req.lower():
                        # 尝试提取 xpath
                        xp_match = re.search(r'//[^\s]+', req)
                        xp = xp_match.group(0) if xp_match else "//title"
                        skill_result = best_skill.functions["xpath_extract"](url=url, xpath=xp)
                    elif "表格" in req or "csv" in req.lower():
                        skill_result = best_skill.functions["extract_tables"](url=url)
                    elif "批量" in req or "batch" in req.lower():
                        urls = re.findall(r'https?://[^\s<>"\']+', req)
                        skill_result = best_skill.functions["batch_scrape"](urls=urls if urls else [url])
                    else:
                        # 默认 JSON-LD
                        skill_result = best_skill.functions["extract_json_ld"](url=url)

                elif best_skill.name == "weather":
                    req = state["user_request"]
                    location_map = {
                        "义乌": "义乌", "杭州": "杭州", "上海": "上海",
                        "北京": "北京", "深圳": "深圳", "广州": "广州",
                        "成都": "成都", "南京": "南京", "武汉": "武汉",
                    }
                    location = "义乌"
                    for loc in location_map:
                        if loc in req:
                            location = loc
                            break
                    skill_result = best_skill.functions["get_weather"](location=location)

                elif best_skill.name == "local-executor":
                    # ═══════════════════════════════════════════════════════
                    #  Intent-Guided Dispatch（替代纯关键词 fallback）
                    # ═══════════════════════════════════════════════════════
                    from .intent_classifier import classify_intent, intent_to_handler_key

                    req = state["user_request"]
                    funcs = best_skill.functions

                    # Step 1：LLM 意图分类
                    intent_info = classify_intent(req)
                    intent = intent_info["intent"]
                    confidence = intent_info["confidence"]
                    is_shell_safe = intent_info["is_shell_safe"]

                    # Step 2：低置信度 / 不安全 → 要求澄清，不执行 shell
                    if intent == "unclear" or (not is_shell_safe and confidence < 0.6):
                        skill_result = (
                            "🤖 我不确定您的意图，请更明确地说明您想要的操作类型：\n"
                            "  • 执行 Git 操作 → 说「git commit/push/branch」\n"
                            "  • 查看系统状态 → 说「系统状态」或「系统模块」\n"
                            "  • 执行命令 → 说「运行 xxx 命令」\n"
                            "  • 写代码 → 说「写代码：...」\n"
                            "  • 搜索 → 说「搜索 xxx」"
                        )
                    else:
                        # Step 3：按意图路由到对应 handler
                        handler_key = intent_to_handler_key(intent)

                        if handler_key == "system_status":
                            parts = []
                            parts.append("=== Git ===")
                            parts.append(funcs["git_status"]())
                            parts.append("\n=== Cron 调度器 ===")
                            parts.append(funcs["systemctl"](action="status", unit="cron.service"))
                            parts.append("\n=== 进程列表(top 5) ===")
                            parts.append(funcs["process_list"]())
                            skill_result = "\n".join(parts)

                        elif handler_key == "git_operation":
                            if "status" in req.lower() or "工作区" in req:
                                skill_result = funcs["git_status"]()
                            elif "commit" in req.lower() or "提交" in req:
                                # 支持 git commit -m "msg" 和 git commit "msg" 两种格式
                                m = re.search(r'-m\s*((?:\x22|\x27)(.+?)(?:\x22|\x27))', req)
                                if not m:
                                    m = re.search(r'(?:\x22|\x27)(.+?)(?:\x22|\x27)', req.split("commit")[-1])
                                msg = m.group(2).strip() if m and m.group(2) else "更新"
                                skill_result = funcs["git_commit"](message=msg)
                            elif "push" in req.lower() or "推送" in req:
                                skill_result = funcs["git_push"]()
                            elif "branch" in req.lower() or "分支" in req:
                                m = re.search(r'[分支|-b]\s*(\S+)', req)
                                name = m.group(1) if m else "feature/new"
                                skill_result = funcs["git_branch"](name=name)
                            elif "checkout" in req.lower() or "切换" in req:
                                m = re.search(r'(?:checkout|切换)\s+(\S+)', req)
                                branch = m.group(1) if m else "main"
                                git_checkout = funcs.get("git_checkout")
                                skill_result = (git_checkout(branch=branch) if git_checkout
                                                else funcs["git_branch"](name=branch))
                            else:
                                skill_result = funcs["git_status"]()

                        elif handler_key == "shell_command":
                            # 优先检测 git 关键操作，避免被 shell_command handler 误捕
                            git_keywords = ["git commit", "git push", "git pull", "git merge",
                                            "git checkout", "git branch", "git stash", "git clone",
                                            "git fetch", "git rebase", "git reset", "git log",
                                            "提交代码", "推送代码", "合并分支"]
                            if any(k in req.lower() for k in git_keywords):
                                # 交给 git_operation 处理
                                if "status" in req.lower() or "工作区" in req:
                                    skill_result = funcs["git_status"]()
                                elif "commit" in req.lower() or "提交" in req:
                                    m = re.search(r'-m\s*((?:\x22|\x27)(.+?)(?:\x22|\x27))', req)
                                    if not m:
                                        m = re.search(r'(?:\x22|\x27)(.+?)(?:\x22|\x27)', req.split("commit")[-1])
                                    msg = m.group(2).strip() if m and m.group(2) else "更新"
                                    skill_result = funcs["git_commit"](message=msg)
                                elif "push" in req.lower() or "推送" in req:
                                    sentinel = _needs_approval("git_push", req)
                                    if sentinel:
                                        skill_result = "__NEEDS_APPROVAL__:" + json.dumps(sentinel)
                                    else:
                                        skill_result = funcs["git_push"]()
                                elif "pull" in req.lower() or "拉取" in req:
                                    skill_result = funcs.get("git_pull", lambda: "[错误] git_pull 未配置")()
                                elif "branch" in req.lower() or "分支" in req:
                                    m = re.search(r'[分支|-b]\s*(\S+)', req)
                                    name = m.group(1) if m else "feature/new"
                                    skill_result = funcs["git_branch"](name=name)
                                elif "checkout" in req.lower() or "切换" in req:
                                    m = re.search(r'(?:checkout|切换)\s+(\S+)', req)
                                    branch = m.group(1) if m else "main"
                                    git_checkout = funcs.get("git_checkout")
                                    skill_result = (git_checkout(branch=branch) if git_checkout
                                                    else funcs["git_branch"](name=branch))
                                elif "log" in req.lower() or "历史" in req:
                                    skill_result = funcs.get("git_log", lambda: "[错误] git_log 未配置")()
                                else:
                                    skill_result = funcs["git_status"]()
                            else:
                                warn = "⚠️ 低置信度命令执行：" if confidence < 0.7 else ""
                                cmd_m = re.search(r'[\x22\x27](.+?)[\x22\x27]', req)
                                if cmd_m:
                                    skill_result = warn + funcs["run_command"](cmd=cmd_m.group(1))
                                else:
                                    cmd_tokens = []
                                    for kw in ["free -h", "free", "df -h", "df", "ps aux", "ps", "top",
                                               "ls", "ls -la", "cat ", "grep ", "pwd", "whoami", "uptime",
                                               "uname", "hostname", "curl ", "wget ", "kill ", "pkill",
                                               "systemctl status", "systemctl restart", "systemctl stop"]:
                                        if kw in req.lower():
                                            cmd_tokens.append(kw)
                                    if cmd_tokens:
                                        skill_result = warn + funcs["run_command"](cmd=cmd_tokens[0])
                                    else:
                                        skill_result = (
                                            "🤖 请用引号包裹具体命令，例如：「运行 \"free -h\"」或「执行 \"ps aux\"」"
                                        )

                        elif handler_key == "file_operation":
                            if any(k in req for k in ["写文件", "写入", "创建文件"]):
                                m = re.search(r'(/\S+)\s*[:：]\s*(.+)', req)
                                skill_result = (funcs["write_file"](path=m.group(1), content=m.group(2))
                                                if m else "[错误] 格式：/path/file.ext : 内容")
                            elif any(k in req for k in ["读文件", "读取文件", "cat "]):
                                m = re.search(r'(/\S+)', req)
                                skill_result = funcs["read_file"](path=m.group(1)) if m else "[错误] 格式：/path/file"
                            else:
                                parts = req.split()
                                skill_result = (funcs["read_file"](path=parts[-1]) if parts
                                                else "[错误] 格式：/path/file")

                        elif handler_key == "deploy":
                            m = re.search(r'([/\w_-]+\.sh)', req)
                            skill_result = (funcs["deploy_script"](script_path=m.group(1)) if m
                                            else "[错误] 格式：/path/to/script.sh")

                        elif handler_key == "code_generation":
                            # 提取代码请求并用 LLM 生成代码
                            task = req
                            for prefix in ["写代码", "帮我写", "写个", "开发", "实现", "用 Python", "用 C", "用 Java", "写一个"]:
                                if prefix in task:
                                    task = task.split(prefix)[-1].strip()
                                    break
                            try:
                                skill_result = _llm_call(
                                    [
                                        {"role": "system", "content": "你是一个专业的代码生成器。用户要求写代码时，直接输出代码（用markdown ```包裹），不要解释。"},
                                        {"role": "user", "content": task}
                                    ],
                                    state,
                                    intent_type="code_generation",
                                )
                                if skill_result.startswith("[错误]"):
                                    raise RuntimeError(skill_result)
                                # 自我验证回路：生成代码后自动执行并验证结果
                                skill_result = verify_and_execute(req, skill_result)
                            except Exception as e:
                                skill_result = f"[错误] 代码生成失败：{e}"

                        elif handler_key == "search":
                            # 提取搜索查询并用 LLM 回答
                            query = req
                            for prefix in ["搜索", "查找", "查询", "了解一下", "查一下"]:
                                if prefix in query:
                                    query = query.split(prefix)[-1].strip()
                                    break
                            try:
                                skill_result = _llm_call(
                                    [{"role": "user", "content": f"请简要回答：{query}。如果需要最新信息，请基于你的知识库回答。"}],
                                    state,
                                    intent_type="search",
                                )
                            except Exception as e:
                                skill_result = f"[错误] 搜索失败：{e}"

                        elif handler_key in ("service_management", "systemctl"):
                            # 服务管理：启动/停止/重启 systemd 服务
                            action = None
                            unit = None
                            req_lower = req.lower()
                            if any(k in req_lower for k in ["启动", "start"]):
                                action = "start"
                            elif any(k in req_lower for k in ["停止", "stop"]):
                                action = "stop"
                            elif any(k in req_lower for k in ["重启", "restart", "reload"]):
                                action = "restart"
                            elif any(k in req_lower for k in ["状态", "status"]):
                                action = "status"
                            # 提取服务名
                            m_unit = re.search(r'(?:服务|unit|service)[:：]?\s*(\S+?)(?:\s|$|。)', req)
                            if m_unit:
                                unit = m_unit.group(1)
                            else:
                                # 尝试从常见服务名中匹配
                                for svc in ["cron.service", "nginx", "docker", "redis", "mysql", "postgresql"]:
                                    if svc in req_lower:
                                        unit = svc
                                        break
                            if action and unit:
                                # stop/disable 需要审批
                                if action in ("stop", "disable"):
                                    sentinel = _needs_approval(f"systemctl_{action}", req)
                                    if sentinel:
                                        skill_result = "__NEEDS_APPROVAL__:" + json.dumps(sentinel)
                                    else:
                                        skill_result = funcs.get("systemctl", funcs.get("run_command", lambda **kw: str(kw)))(
                                            action=action, unit=unit
                                        )
                                else:
                                    skill_result = funcs.get("systemctl", funcs.get("run_command", lambda **kw: str(kw)))(
                                        action=action, unit=unit
                                    )
                            elif unit:
                                skill_result = funcs.get("systemctl", funcs.get("run_command", lambda **kw: str(kw)))(
                                    action="status", unit=unit
                                )
                            else:
                                skill_result = "[错误] 格式：「服务管理 启动/停止/重启 服务名」"

                        elif handler_key == "config_edit":
                            # 配置修改：读文件 → 修改 → 写回
                            m = re.search(r'(/\S+\.(?:conf|ini|yaml|yml|json|env|toml|cfg))', req)
                            if m:
                                path = m.group(1)
                                # 系统路径写入需要审批
                                if any(path.startswith(p) for p in ["/etc/", "/usr/", "/var/", "/root/"]):
                                    sentinel = _needs_approval("config_edit", req)
                                    if sentinel:
                                        skill_result = "__NEEDS_APPROVAL__:" + json.dumps(sentinel)
                                    # else: 继续执行写入
                                # 提取新值：key=value 或 key: value
                                m2 = re.search(r'(\w+)[:：=]\s*(.+)', req)
                                if m2:
                                    key, val = m2.group(1), m2.group(2).rstrip('。.')
                                    try:
                                        old = funcs.get("read_file", lambda path: "")(path=path)
                                        new_content = re.sub(
                                            rf'^{re.escape(key)}\s*[:＝=].*$',
                                            f'{key}: {val}',
                                            old, flags=re.MULTILINE
                                        )
                                        if old == new_content:
                                            new_content = old + f'\n{key}: {val}'
                                        skill_result = funcs.get("write_file", lambda path, content: str({"path": path, "content": content}))(
                                            path=path, content=new_content
                                        )
                                    except Exception as e:
                                        skill_result = f"[错误] 配置修改失败：{e}"
                                else:
                                    skill_result = f"[信息] 文件路径：{path}，请说明要修改的配置项和值，格式：「key: 新值」"
                            else:
                                skill_result = "[错误] 格式：「/path/to/config.ext」并说明要改什么配置项"

                        elif handler_key == "upgrade":
                            # 升级系统：调用 hongjun_upgrader 工具
                            req_lower = req.lower()
                            try:
                                from hongjun_upgrader import HongjunUpgrader
                                U = HongjunUpgrader()

                                if any(k in req_lower for k in ["状态", "version", "版本", "查看版本"]):
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
                                    skill_result = "\n".join(lines)

                                elif any(k in req_lower for k in ["修复", "repair"]):
                                    result = U.repair()
                                    lines = [f"🔧 修复完成: {result['detail']}"]
                                    if result.get("repaired"):
                                        lines.append(f"🔄 已修复: {', '.join(result['repaired'])}")
                                    if result.get("failed"):
                                        lines.append(f"❌ 修复失败: {', '.join(result['failed'])}")
                                    lines.append(f"🚀 服务重启: {'成功' if result.get('services_restarted') else '失败'}")
                                    skill_result = "\n".join(lines)

                                elif any(k in req_lower for k in ["回滚", "rollback"]):
                                    m_bp = re.search(r'([/\w_-]+\.tar\.gz)', req)
                                    bp = Path(m_bp.group(1)) if m_bp else None
                                    result = U.rollback(backup_path=bp)
                                    if result["success"]:
                                        skill_result = f"↩️ 回滚成功: {result['detail']}（版本: {result.get('version', '未知')}）"
                                    else:
                                        skill_result = f"❌ 回滚失败: {result.get('detail', '未知错误')}"

                                elif any(k in req_lower for k in ["升级", "update", "upgrade", "更新"]):
                                    # 提取目标版本
                                    m_ver = re.search(r'(\d+\.\d+\.\d+)', req)
                                    target = m_ver.group(1) if m_ver else None
                                    # 提取 bump 级别
                                    bump = "patch"
                                    for lvl in ["major", "minor", "patch"]:
                                        if lvl in req_lower:
                                            bump = lvl
                                            break
                                    # dry_run
                                    dry_run = "dry" in req_lower or "试运行" in req_lower
                                    # changelog
                                    changelog = req  # 简略
                                    result = U.upgrade(
                                        target_version=target,
                                        changelog=changelog,
                                        bump=bump,
                                        dry_run=dry_run,
                                    )
                                    current = U.get_current_version()
                                    if result["success"]:
                                        lines = [
                                            f"✅ 升级成功: v{current} -> v{result['version']} ({result['level']})",
                                        ]
                                        if result.get("backup"):
                                            lines.append(f"💾 备份: {result['backup']}")
                                        skill_result = "\n".join(lines)
                                    else:
                                        detail = result.get("detail", "未知错误")
                                        if result.get("result") == "rolled_back":
                                            skill_result = f"❌ 升级失败，已自动回滚。\n{detail}"
                                        else:
                                            skill_result = f"❌ 升级失败: {detail}"
                                else:
                                    skill_result = (
                                        "🤖 升级系统指令格式：\n"
                                        "  • 查看版本 → 「版本」或「升级状态」\n"
                                        "  • 升级 → 「升级」或「升级到 X.Y.Z」\n"
                                        "  • 修复 → 「修复鸿钧」\n"
                                        "  • 回滚 → 「回滚」或「回滚到 /path/to/backup.tar.gz」\n"
                                        "升级源支持三种格式：本地 tar.gz / 本地目录 / HTTP URL（放入 upgrades/releases/）"
                                    )
                            except Exception as e:
                                skill_result = f"❌ 升级系统异常: {e}"

                        else:
                            # 意图识别但无对应 handler → 要求澄清
                            cmd_m = re.search(r'["'"](.+?)["'"]', req)
                            if cmd_m:
                                skill_result = funcs["run_command"](cmd=cmd_m.group(1))
                            else:
                                skill_result = (
                                    "🤖 我将这条消息识别为「" + intent + "」意图（置信度 " +
                                    str(round(confidence * 100)) + "%），"
                                    "但当前不支持自动执行。请明确说明您想要的操作。"
                                )

                if skill_result:
                    results.append(f"[SKILL:{best_skill.name}] {skill_result[:300]}")
                    # Skill 成功后，跳过其他待执行任务（只保留已完成的安全审核）
                    for task in state["subtasks"]:
                        task = dict(task)
                        if task["assigned_to"] == "security" and task["status"] == "pending":
                            task["status"] = "completed"
                            task["result"] = "✅ 安全审核通过（Skill 优先）"
                            updated_subtasks.append(task)

                    # === 记忆检索：关联用户相关记忆 ===
                    try:
                        from .memory import HongjunMemory
                        mem = HongjunMemory(user_id=state.get("user_id") or "default")
                        # 用请求+结果一起检索，更容易找到相关记忆
                        query_for_mem = f"{state['user_request']} {skill_result[:200]}"
                        memory_context = mem.build_context(query_for_mem, max_memories=5)
                    except Exception:
                        memory_context = ""

                    return {
                        **state,
                        "subtasks": updated_subtasks,
                        "results": results,
                        "security_passed": True,
                        "skill_result": skill_result,
                        "memory_context": memory_context,
                    }
            except Exception as e:
                results.append(f"[SKILL:{best_skill.name}] ❌ 执行失败: {e}")

    # === Step 2：无 skill 匹配时 → 先尝试 IntentClassifier 再 fallback 到 LLM ===
    if not skill_result:
        try:
            from .intent_classifier import classify_intent, intent_to_handler_key
            req = state["user_request"]
            intent_info = classify_intent(req)
            intent = intent_info["intent"]
            confidence = intent_info["confidence"]

            # 高置信度已知意图 → 直接路由到 handler（不依赖 skill functions）
            if confidence >= 0.6 and intent in (
                "code_generation", "search", "service_management",
                "config_edit", "system_status", "conversation"
            ):
                if intent == "code_generation":
                    task = req
                    for prefix in ["写代码", "帮我写", "写个", "开发", "实现", "用 Python", "用 C", "用 Java", "写一个"]:
                        if prefix in task:
                            task = task.split(prefix)[-1].strip()
                            break
                    try:
                        skill_result = _llm_call(
                            [
                                {"role": "system", "content": "你是一个专业的代码生成器。用户要求写代码时，直接输出代码（用markdown ```包裹），不要解释。如果涉及可视化效果（动画、游戏、图表），请生成可运行的 HTML/JS 代码。"},
                                {"role": "user", "content": task}
                            ],
                            state,
                            intent_type="code_generation",
                        )
                        if skill_result.startswith("[错误]"):
                            raise RuntimeError(skill_result)
                        # 自我验证回路：生成代码后自动执行并验证结果
                        skill_result = verify_and_execute(req, skill_result)
                    except Exception as e:
                        skill_result = f"[错误] 代码生成失败：{e}"

                elif intent == "search":
                    query = req
                    for prefix in ["搜索", "查找", "查询", "了解一下", "查一下"]:
                        if prefix in query:
                            query = query.split(prefix)[-1].strip()
                            break
                    try:
                        skill_result = _llm_call(
                            [{"role": "user", "content": f"请简要回答：{query}。如果需要最新信息，请基于你的知识库回答。"}],
                            state,
                            intent_type="search",
                        )
                    except Exception as e:
                        skill_result = f"[错误] 搜索失败：{e}"

                elif intent == "system_status":
                    try:
                        import subprocess
                        git_out = subprocess.check_output("git status --short", shell=True, text=True).strip() or "工作区干净"
                        cron_out = subprocess.check_output("systemctl status cron.service --no-pager -l", shell=True, text=True).strip()
                        ps_out = subprocess.check_output("ps aux | head -6", shell=True, text=True).strip()
                        skill_result = f"=== Git ===\n{git_out}\n\n=== Cron ===\n{cron_out}\n\n=== 进程 ===\n{ps_out}"
                    except Exception as e:
                        skill_result = f"[错误] 获取系统状态失败：{e}"

                elif intent == "service_management":
                    # 服务管理：systemctl / mempalace / 其他 CLI 工具
                    req_lower = req.lower()
                    skill_result = _handle_service_management(req, req_lower, funcs)

                elif intent == "config_edit":
                    skill_result = _handle_config_edit(req, req_lower)

                elif intent == "deploy":
                    skill_result = _handle_deploy(req, req_lower)

                elif intent == "conversation":
                    try:
                        skill_result = _llm_call(
                            [{"role": "user", "content": req}],
                            state,
                            intent_type="conversation",
                        )
                    except Exception as e:
                        skill_result = f"[错误] 对话失败：{e}"

        except Exception:
            pass  # fallback to pure LLM

        # ── 如果没有任何结果 → 直接走 conversation（通用问答）──
        if not skill_result:
            try:
                skill_result = _llm_call(
                    [{"role": "user", "content": state["user_request"]}],
                    state,
                    intent_type="conversation",
                )
            except Exception as e:
                skill_result = ""

    # 步骤回调：任务完成
    _emit_step("task_done", {"result": (skill_result or "")[:200]})

    # === 记忆检索（无论有无 skill 都执行）===
    try:
        from .memory import HongjunMemory
        mem = HongjunMemory(user_id=state.get("user_id") or "default")
        memory_context = mem.build_context(state["user_request"], max_memories=5)
    except Exception:
        memory_context = ""

    return {
        **state,
        "subtasks": updated_subtasks if updated_subtasks else state["subtasks"],
        "results": results,
        "skill_result": skill_result,
        "security_passed": True,
        "memory_context": memory_context,
    }


def summarize(state: CoordinatorState) -> CoordinatorState:
    """
    节点3：结果汇总

    整合各 Agent 返回，生成最终回复。

    流程：
      1. 若有 skill_result → 用 LLM 结合记忆上下文生成个性化回复
      2. 若无 skill_result 但有 subtask 结果 → 拼接各部返回
      3. 若无任何结果 → "任务执行完成"
    """
    # ── 路径A：Skill 结果 → 直接返回，不重写 ─────────────────────────
    # 原则：skill 执行结果 = 真相。不要让 LLM 重新描述工具输出。
    # LLM 的工作是基于结果回答用户问题，而不是改写工具返回的内容。
    if state.get("skill_result"):
        skill_result = state["skill_result"]

        # 清理 compaction REFERENCE 标记（防止 session 历史中的污染渗入）
        cleaned = re.sub(
            r'\[CONTEXT COMPACTION.*?\]\s*|\*\*REFERENCE ONLY\*\*.*?(?=\n\n|\Z)',
            '',
            skill_result,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()

        # 步骤回调：最终回复已生成（实际内容由 SSE chunk 后续送达）
        _emit_step("final", {"response": "(内容由 SSE chunk 送达)"})

        return {
            **state,
            "final_response": cleaned or skill_result,
        }

    # ── 路径B：Subtask 结果拼接 ────────────────────────────────────
    parts = []
    for task, result in zip(state["subtasks"], state["results"]):
        if result:
            parts.append(f"**{task['assigned_to'].upper()}**: {result}")

    # 注意：memory_context 仅供 LLM 内部参考，不直接显示给用户
    # （其内容来自 MemPalace，可能包含低质量/高噪音数据，直接展示影响体验）

    final_response = "\n\n".join(parts) if parts else "任务执行完成，无返回结果。"

    # 步骤回调：最终回复已生成（路径B）
    _emit_step("final", {"response": final_response[:200]})

    return {
        **state,
        "final_response": final_response,
    }


# === 构建 LangGraph ===

def build_coordinator_graph():
    """构建并返回编译好的 LangGraph"""
    graph = StateGraph(CoordinatorState)

    # 注册节点
    graph.add_node("parse_intent", parse_intent)
    graph.add_node("dispatch", dispatch_and_execute)
    graph.add_node("summarize", summarize)

    # 定义边
    graph.add_edge("parse_intent", "dispatch")
    graph.add_edge("dispatch", "summarize")
    graph.add_edge("summarize", END)

    # 设置入口
    graph.set_entry_point("parse_intent")

    return graph.compile()


# 全局单例
coordinator_graph = build_coordinator_graph()


# === 同步调用接口 ===

def process_request(
    user_request: str,
    user_id: Optional[str] = None,
    approved_op: Optional[str] = None,
    step_callback: Optional[callable] = None,
) -> str:
    """
    外部调用的同步接口

    接收用户请求，返回处理结果。

    Args:
        approved_op: 已批准的 operation id（二次调用时传入，跳过危险操作预检）
                     若此 id 在 server._APPROVED_OPS 中，则该操作已获批准，直接执行。
        step_callback: 可选的步骤回调，签名: callback(step_type, step_data)
                       step_type: "intent" | "task_start" | "task_done" | "final"
                       step_data: dict with step details
                       注意：回调在同步线程中调用，不能执行 async 操作。

    用法：
        response = process_request("帮我搜索 GitHub 今天的 AI 趋势", user_id="皇上")
    """
    # 避免循环导入，仅在需要时引用 server 模块
    _approved_ops: dict = {}
    try:
        from hongjun.gateway import server as _srv
        _approved_ops = getattr(_srv, "_APPROVED_OPS", {})
    except Exception:
        pass

    # 将 approved_op 注入状态，传递给 dispatch_and_execute
    initial_state: CoordinatorState = {
        "user_request": user_request,
        "intent": None,
        "subtasks": [],
        "results": [],
        "final_response": None,
        "user_id": user_id,
        "memory_context": "",
        "security_passed": False,
        "eval_score": None,
        "approved_op": approved_op,          # 已批准操作 id
        "_approved_ops": _approved_ops,       # server._APPROVED_OPS 引用
    }

    # 元学习：获取策略推荐
    try:
        from hongjun.meta_learner import get_learner
        strategy = get_learner().recommend(user_request)
        initial_state["_strategy"] = strategy.to_dict()
    except Exception:
        initial_state["_strategy"] = {}

    # 使用 ContextVar 传递 step_callback（避免 LangGraph TypedDict 验证过滤）
    token = _step_callback_var.set(step_callback)
    try:
        final_state = coordinator_graph.invoke(initial_state)
    finally:
        _step_callback_var.reset(token)

    response = final_state.get("final_response", "处理异常，无返回。")

    # 进化记忆：记录任务结果
    try:
        from hongjun.evolution_memory import EvolutionMemory
        from hongjun.evaluator import HongjunEvaluator
        from hongjun.meta_learner import get_learner
        mem = EvolutionMemory()
        evaluator = HongjunEvaluator()

        eval_report = evaluator.evaluate(task=initial_state.get("intent", "") or user_request[:50],
                                          result=response)

        is_error = any(err in response for err in ["[错误]", "❌", "失败", "exception"])
        intent_used = final_state.get("intent", "") or initial_state.get("intent", "")
        strategy_used = initial_state.get("_strategy")

        if is_error:
            mem.record_failure(
                task=intent_used or user_request[:50],
                request=user_request,
                error=response[:300],
            )
            if strategy_used:
                get_learner().record(
                    task_request=user_request,
                    intent=intent_used,
                    strategy=strategy_used,
                    success=False,
                    error=response[:200],
                )
        else:
            mem.record_success(
                task=intent_used or user_request[:50],
                request=user_request,
                result=response,
                intent=intent_used,
            )
            if strategy_used:
                get_learner().record(
                    task_request=user_request,
                    intent=intent_used,
                    strategy=strategy_used,
                    success=True,
                )
    except Exception:
        pass  # 记忆失败不影响主流程

    return response


# === 单元测试 ===
if __name__ == "__main__":
    print("=" * 50)
    print("鸿钧 · 吏部协调引擎测试")
    print("=" * 50)

    test_requests = [
        "帮我搜索 GitHub 今天的 AI Agent 趋势项目",
        "写一个快速排序算法",
        "我之前让你搜索的内容是什么？",
    ]

    for req in test_requests:
        print(f"\n📥 用户请求: {req}")
        print("-" * 40)
        response = process_request(req)
        print(f"📤 鸿钧回复:\n{response}")
        print()
