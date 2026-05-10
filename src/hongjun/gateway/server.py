"""
鸿钧 · Gateway HTTP Server

基于 uvicorn + aiohttp 的异步 HTTP Gateway。

端点：
  POST /chat              创建/继续会话，返回 LLM 响应
  GET  /status            Gateway 健康状态
  GET  /sessions          列出所有会话
  GET  /sessions/{id}     会话详情（含消息）
  POST /sessions/{id}/shutdown  关闭会话
  POST /shutdown          关闭 Gateway
  GET  /metrics           性能指标

Request body (POST /chat):
  {
    "message": "用户消息",
    "session_id": "可选，指定会话",
    "model": "可选，默认 MiniMax-M2.7"
  }

Response:
  {
    "session_id": "...",
    "response": "LLM 响应文本",
    "state": "ACTIVE",
    "message_count": 5
  }
"""

import asyncio
import json
import logging
import re
import signal
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from aiohttp import web, WSMsgType

# 内部模块
from .db import HongjunDB
from .session import SessionManager, SessionState, Session
from .queue import RequestQueue, TaskPriority


# ── 工具调用审批管理器 ───────────────────────────────────────────────

DANGEROUS_PATTERNS = [
    # Shell 危险操作（支持中英文 run/运行/execute/执行）
    (r'(run|运行|execute|exec|执行)\b[^<>;&|`$]+\brm\s+-rf', "删除文件系统（rm -rf）", 10),
    (r'(run|运行|execute|exec|执行)\b[^<>;&|`$]+\bdel\s+/[fq]', "删除文件系统（del）", 10),
    (r'(run|运行|execute|exec|执行)\b[^<>;&|`$]+\bmkfs', "格式化磁盘", 10),
    (r'(run|运行|execute|exec|执行)\b[^<>;&|`$]+\bdd\s+if=', "直接磁盘写入（dd）", 10),
    (r'(run|运行|execute|exec|执行)\b[^<>;&|`$]+(reboot|shutdown|init\s+0|init\s+6)', "重启/关机", 10),
    (r'(run|运行|execute|exec|执行)\b[^<>;&|`$]+(kill\s+-9|kill\s+-\d| pkill)', "强制终止进程", 8),
    (r'curl[^<>;&|`$]+\|.*sh|wget[^<>;&|`$]+\|.*sh', "危险下载管道执行", 10),
    (r'chmod\s+777|chmod\s+-\w+x', "放宽权限（chmod 777）", 7),
    (r'sudo\s+su|sudo\s+.*\s+su\b', "提权切换用户（sudo su）", 9),
    (r':(){ :|:& };:', "Fork 炸弹", 10),  # :(){ :|:& };:
    # Git 危险操作
    (r'git\s+push\s+.*--force|git\s+push\s+.*-f\b', "强制推送（--force）", 9),
    (r'git\s+push\s+.*delete|git\s+push\s+.*:\w+', "删除远程分支/引用", 9),
    (r'git\s+reset\s+.*--hard|git\s+reset\s+.*--mixed', "重置提交历史", 8),
    (r'git\s+rebase\s+.*-i\s+HEAD~\d+|git\s+filter-branch', "修改提交历史（rebase -i）", 8),
    (r'git\s+push\s+origin\s+--mirror', "镜像推送（覆盖远程）", 10),
    # 服务管理
    (r'(systemctl|service)\s+(stop|disable|restart)\s+.+\.(service|socket)', "服务启停", 5),
    # 文件写入系统路径
    (r'(write|edit|patch|tee)\s+[^<>;&|`$]*(/etc/|/usr/|/var/|/root/)', "系统路径写入", 7),
]

APPROVAL_TIMEOUT_SEC = 120


class PendingApproval:
    """单条待审批操作"""

    def __init__(
        self,
        approval_id: str,
        operation: str,
        command: str,
        reason: str,
        severity: int,
    ):
        self.id = approval_id
        self.operation = operation  # "shell_command" | "git_push" | ...
        self.command = command
        self.reason = reason  # 中文说明
        self.severity = severity  # 1-10
        self.created_at = time.time()
        self.future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.approved: Optional[bool] = None
        self.result: Optional[str] = None  # 执行结果（审批后填入）


class ApprovalManager:
    """管理所有待审批操作"""

    def __init__(self):
        self._pending: dict[str, PendingApproval] = {}
        self._lock = asyncio.Lock()

    def check(self, operation: str, command: str) -> Optional[PendingApproval]:
        """检查命令是否危险，若是返回 PendingApproval（未审批）"""
        for pattern, reason, severity in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                approval_id = str(uuid.uuid4())[:8]
                p = PendingApproval(approval_id, operation, command, reason, severity)
                return p
        return None  # 安全，无需审批

    async def register(self, approval: PendingApproval) -> str:
        """注册待审批项，返回 approval_id"""
        async with self._lock:
            self._pending[approval.id] = approval
        return approval.id

    async def get(self, approval_id: str) -> Optional[PendingApproval]:
        async with self._lock:
            return self._pending.get(approval_id)

    async def list_pending(self) -> list[dict]:
        async with self._lock:
            now = time.time()
            return [
                {
                    "id": p.id,
                    "operation": p.operation,
                    "command": p.command,
                    "reason": p.reason,
                    "severity": p.severity,
                    "age_seconds": round(now - p.created_at),
                }
                for p in self._pending.values()
                if not p.future.done()
            ]

    async def approve(self, approval_id: str) -> bool:
        """审批通过（设置 future.result），返回是否成功"""
        async with self._lock:
            p = self._pending.get(approval_id)
        if not p or p.future.done():
            return False
        p.approved = True
        p.future.set_result("approved")
        return True

    async def reject(self, approval_id: str) -> bool:
        """审批拒绝"""
        async with self._lock:
            p = self._pending.get(approval_id)
        if not p or p.future.done():
            return False
        p.approved = False
        p.future.set_result("rejected")
        return True

    async def resolve(self, approval_id: str, result: str):
        """执行完成后清理"""
        async with self._lock:
            self._pending.pop(approval_id, None)

# 核心系统模块（延迟导入避免循环依赖）
_orchestrator_module = None
_memory_module = None
_security_module = None
_evaluator_module = None

# 已批准操作的缓存（防止二次调用时重复审批）
# key = approval_id，value = True
_APPROVED_OPS: dict[str, bool] = {}


def _get_orchestrator():
    global _orchestrator_module
    if _orchestrator_module is None:
        try:
            from .. import orchestrator as m
            _orchestrator_module = m
        except ImportError:
            logger.warning("orchestrator module not available")
    return _orchestrator_module


def _get_memory():
    global _memory_module
    if _memory_module is None:
        try:
            from .. import memory as m
            _memory_module = m
        except ImportError:
            logger.warning("memory module not available")
    return _memory_module


def _get_security():
    global _security_module
    if _security_module is None:
        try:
            from .. import security as m
            _security_module = m
        except ImportError:
            logger.warning("security module not available")
    return _security_module


def _get_evaluator():
    global _evaluator_module
    if _evaluator_module is None:
        try:
            from .. import evaluator as m
            _evaluator_module = m
        except ImportError:
            logger.warning("evaluator module not available")
    return _evaluator_module

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.gateway")


# ── Request context tracing helper ───────────────────────────────────────────

def _trace_request(
    logger, handler: str, trace_id: str, /, *, session_id: str = None, model: str = None, **extra
):
    """Log request start at DEBUG level with full context fields."""
    logger.debug(
        "request_start",
        handler=handler,
        trace_id=trace_id,
        session_id=session_id,
        model=model,
        **extra,
    )


def _trace_response(
    logger, handler: str, trace_id: str, /, *, status: int, latency_s: float = None, error: str = None, **extra
):
    """Log request end at INFO (errors) or DEBUG (success) level."""
    if error:
        logger.warning(
            "request_end",
            handler=handler,
            trace_id=trace_id,
            status=status,
            error=error,
            **extra,
        )
    else:
        logger.info(
            "request_end",
            handler=handler,
            trace_id=trace_id,
            status=status,
            latency_s=latency_s,
            **extra,
        )


def _clean_response(text: str) -> str:
    import re
    text = re.sub(
        r'\[CONTEXT COMPACTION.*?\]|\*\*REFERENCE ONLY\*.\*?.*?(?=\n\n|\Z)',
        ' ', text, flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r'<think>[\s\S]*?</think>', ' ', text)
    text = re.sub(r'<think>[^\n]*', ' ', text)
    text = re.sub(r'^#{1,3}\s*(思考|分析|thought|reflection).*\n', ' ', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ── LLM 模块（延迟导入）─────────────────────────────────────────────

_llm_module = None  # 模块级初始化，避免 ImportError 时 return 抛 NameError


def _get_llm():
    """延迟导入 LLM 模块（避免循环依赖）"""
    global _llm_module
    if _llm_module is None:
        try:
            from .. import llm as imported
            _llm_module = imported
        except ImportError:
            logger.warning("LLM module not available, using placeholder")
    return _llm_module


# ── 飞书通道 ─────────────────────────────────────────────────────

_feishu_handler = None
_mcp_server = None


async def _start_feishu(gateway, poll_interval: float = 5.0):
    """在 Gateway 启动时自动启动飞书通道（WebSocket 实时事件）"""
    global _feishu_handler
    try:
        from ..feishu_client import start_feishu
        gateway_url = f"http://{gateway.host}:{gateway.port}"
        _feishu_handler = await start_feishu(
            gateway_url=gateway_url,
            mode="ws",
        )
        logger.info("飞书通道已启动（WebSocket 实时模式）")
    except Exception as e:
        logger.warning(f"飞书通道启动失败: {e}")


async def _stop_feishu():
    global _feishu_handler
    if _feishu_handler:
        await _feishu_handler.stop()
        _feishu_handler = None


# ── MCP Server ─────────────────────────────────────────────────────

async def _start_mcp(gateway):
    """在 Gateway 启动时自动启动 MCP Server（HTTP Streamable）"""
    global _mcp_server
    try:
        from ..protocol.mcp_server import create_mcp_server
        _mcp_server = create_mcp_server()
        # MCP HTTP Server 使用独立端口（避免与 Gateway 冲突）
        mcp_port = 20831
        asyncio.create_task(
            _mcp_server.run_streamable_http_async(host="localhost", port=mcp_port, path="/mcp")
        )
        logger.info("mcp_server_started", port=mcp_port, path="/mcp")
    except Exception as e:
        logger.warning("mcp_server_start_failed", error=str(e))


async def _stop_mcp():
    global _mcp_server
    if _mcp_server is not None:
        # FastMCP run_streamable_http_async 不返回可停止对象
        # 标记为 None，下次 GC 会清理；实际进程内无法优雅停止 HTTP Server
        _mcp_server = None
        logger.info("mcp_server_stopped")


# ── 全局状态 ──────────────────────────────────────────────────────

_gateway_instance: Optional["HongjunGateway"] = None


def get_gateway() -> Optional["HongjunGateway"]:
    return _gateway_instance


class HongjunGateway:
    """
    鸿钧 Gateway 主类。
    管理 Session、RequestQueue、LLM 集成。
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 20830,
        max_concurrent: int = 4,
    ):
        self.host = host
        self.port = port
        self.started_at = datetime.utcnow().isoformat()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        # 组件初始化
        self.db = HongjunDB()
        self.sessions = SessionManager(self.db)
        self.queue = RequestQueue(max_concurrent=max_concurrent)
        self.approvals = ApprovalManager()  # 工具调用审批管理器

        # LLM 集成占位（Phase G2 真实接入）
        self._llm_handler: Optional[callable] = None

        # Cron 调度器
        try:
            from hongjun_cron import CronManager
            self.cron: CronManager = CronManager()
        except ImportError:
            self.cron = None

        # MCP Server
        try:
            from ..protocol.mcp_server import create_mcp_server
            self.mcp_server = None  # 启动时初始化，见 _start_mcp
        except ImportError:
            self.mcp_server = None

        # 并发锁
        self._active_requests: int = 0
        self._active_lock = asyncio.Lock()

        # 请求计数
        self._request_count = 0
        self._total_latency = 0.0

        # uvicorn app
        self.app = web.Application()
        self._setup_routes()
        self.runner: Optional[web.AppRunner] = None

    # ── LLM 集成 ─────────────────────────────────────────────────

    def set_llm_handler(self, handler: callable):
        """设置 LLM 处理函数 (Phase G2)"""
        self._llm_handler = handler

    async def _call_llm(
        self,
        session: Session,
        message: str,
    ) -> str:
        """
        调用 LLM 获取响应。
        优先用 llm.chat()（Phase G2），暂无则返回占位响应。
        """
        llm = _get_llm()
        if llm is None:
            # 占位响应（Phase G2 之前使用）
            messages = session.get_messages()
            last_msgs = messages[-5:] if len(messages) > 5 else messages
            context_preview = "\n".join(
                f"[{m['role']}]: {m['content'][:80]}"
                for m in last_msgs
            )
            return (
                f"【鸿钧 · Gateway 就绪】\n\n"
                f"收到消息：{message}\n"
                f"会话：{session.id}\n"
                f"消息数：{len(messages)}\n\n"
                f"--- 最近消息 ---\n{context_preview}\n\n"
                f"✅ Gateway 工作正常，LLM 集成待 Phase G2 接入。"
            )

        # Phase G2: 真实 LLM 调用
        messages = session.get_messages()
        try:
            resp = await llm.chat(
                messages=messages,
                model=session.model,
                temperature=0.3,
            )
            return _clean_response(resp.content)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return f"【LLM 错误】{e}"

    # ── 判断是否需要编排器 ────────────────────────────────────────

    @staticmethod
    def _needs_orchestrator(message: str) -> bool:
        """检测是否需要编排器（代码/搜索/记忆/执行类任务）"""
        m = message.lower()
        code_kws = ["写代码", "代码", "开发", "编程", "排序", "算法", "quicksort", "mergesort", "sort", "生成", "实现", "写一个", "写个", "二分", "快排"]
        search_kws = ["搜索", "查", "找", "github", "趋势", "trending", "news", "天气", "气温", "温度", " Weather", "weather"]
        memory_kws = ["记忆", "之前", "上次", "曾经", "你记得"]
        # 执行类：git/服务管理/文件操作/部署
        exec_kws = ["git", "git status", "git commit", "git push", "git branch", "git checkout",
                    "systemctl", "服务", "服务管理", "进程", "ps aux", "kill",
                    "写文件", "读文件", "cat ", "部署", "deploy", "restart", "stop", "start",
                    "系统模块", "系统状态", "健康检查", "health", "gateway", "skills", "skill列表",
                    "cron", "调度器", "模块展示", "展示模块", "模块状态", "运行状态",
                    "检查系统", "查看系统", "内存", "disk", "硬盘", "cpu", "负载",
                    "运行", "run ", "执行命令", "跑命令", "跑个"]
        return any(kw in m for kw in code_kws + search_kws + memory_kws + exec_kws)

    # ── 编排器调用（独立函数，在线程池中运行）─────────────────────

    @staticmethod
    def _call_orchestrator_impl(
        message: str,
        platform: str = "feishu",
        user_id: Optional[str] = None,
        approved_op: Optional[str] = None,
    ) -> str:
        """在线程池中执行的同步编排器调用（仅用于复杂任务）

        Args:
            approved_op: 已批准的 operation id（二次调用时传入，跳过危险操作预检）
        """
        orch = _get_orchestrator()
        if orch is None:
            return "【系统】编排器未可用，请稍后重试。"
        try:
            return orch.process_request(
                message,
                user_id=user_id,
                approved_op=approved_op,
            )
        except TypeError:
            # 兼容旧签名（无 approved_op 参数）
            return orch.process_request(message, user_id=user_id)
        except Exception as e:
            logger.error(f"orchestrator error: {e}")
            return f"【系统错误】处理失败：{e}"

    # ── 请求处理 ─────────────────────────────────────────────────

    async def _process_chat(
        self,
        session: Session,
        message: str,
    ) -> dict:
        """
        处理单条聊天消息。

        完整处理链：
          1. 安全输入审核
          2. 记忆上下文注入
          3. 编排器处理（含工具执行）
          4. 安全输出审核
          5. 质量评估
          6. 记忆存储
          7. 过滤回复标签
        """
        import asyncio
        session.set_state(SessionState.ACTIVE)
        session.add_message("user", message)

        start_ms = time.time() * 1000

        # ── 步骤1：安全输入审核 ──────────────────────────────────
        security_mod = _get_security()
        if security_mod:
            try:
                sec = security_mod.HongjunSecurity()
                passed, error = sec.check_input(message)
                if not passed:
                    response_text = f"【安全审核拦截】{error}"
                    session.add_message("assistant", response_text)
                    session.set_state(SessionState.IDLE)
                    return {
                        "session_id": session.id,
                        "response": response_text,
                        "state": session.state.value,
                        "message_count": session.message_count,
                        "blocked": True,
                    }
            except Exception as e:
                logger.warning(f"security check error: {e}")

        # ── 步骤2：记忆上下文注入 ───────────────────────────────
        memory_context = ""
        memory_mod = _get_memory()
        if memory_mod:
            try:
                mem = memory_mod.HongjunMemory(user_id=session.platform_chat_id or session.platform)
                memory_context = mem.build_context(message)
            except Exception as e:
                logger.warning(f"memory build error: {e}")

        # 组合消息（记忆上下文作为 system 前缀）
        full_message = message
        if memory_context:
            full_message = f"{memory_context}\n\n用户最新请求：{message}"

        # ── 步骤3：路由选择 ─────────────────────────────────────────
        # 通用问答 → 直接 LLM；复杂任务（代码/搜索/记忆）→ 编排器
        # 注意：编排器自己管理记忆注入，不复用 server 层的 memory_context
        if HongjunGateway._needs_orchestrator(message):
            logger.info(f"[route] 复杂任务，走编排器")
            _msg = message          # 传原始消息，编排器自己取记忆
            _plat = session.platform
            _uid = session.platform_chat_id
            try:
                loop = asyncio.get_event_loop()
                response_text = await loop.run_in_executor(
                    None,
                    lambda m=_msg, p=_plat, uid=_uid: HongjunGateway._call_orchestrator_impl(m, p, uid),
                )
                if not response_text:
                    response_text = "【系统】编排器返回了空内容，请稍后重试。"
                    logger.warning(f"Orchestrator returned empty for message: {_msg[:50]}")
            except Exception as e:
                logger.error(f"orchestrator call error: {e}")
                response_text = f"【系统错误】处理失败：{e}"
        else:
            logger.info(f"[route] 通用问答，直接 LLM")
            try:
                llm = _get_llm()
                if llm is None:
                    response_text = "【系统】LLM 未就绪，请稍后重试。"
                else:
                    messages = [{"role": "user", "content": full_message}]
                    resp = await llm.chat(messages=messages, model=session.model or self._default_model, temperature=0.3)
                    response_text = _clean_response(resp.content)
                    if not response_text:
                        response_text = "【系统】LLM 返回了空内容，请重试或换个问法。"
                        logger.warning(f"LLM returned empty content for session {session.id}")
            except Exception as e:
                logger.error(f"LLM call error: {e}")
                response_text = f"【LLM 错误】{e}"
        # ── 步骤4：安全输出审核 ─────────────────────────────────
        if security_mod and response_text:
            try:
                sec = security_mod.HongjunSecurity()
                passed, _ = sec.check_output(response_text)
                if not passed:
                    response_text = "【安全审核】回复内容未通过审核，请重新提问。"
            except Exception as e:
                logger.warning(f"output security check error: {e}")

        # ── 步骤5：质量评估（不阻塞回复，仅记录）───────────────
        eval_report = None
        eval_mod = _get_evaluator()
        if eval_mod and response_text:
            try:
                exec_ms = time.time() * 1000 - start_ms
                ev = eval_mod.HongjunEvaluator()
                eval_report = ev.evaluate(
                    task=message,
                    result=response_text,
                    execution_time_ms=exec_ms,
                )
                if eval_report.overall_score < 0.6:
                    response_text += f"\n\n⚠️ 系统提示：本次回答质量评分 {eval_report.overall_score:.0%}，建议复核关键信息。"
            except Exception as e:
                logger.warning(f"evaluator error: {e}")

        # ── 步骤6：记忆存储 ─────────────────────────────────────
        if memory_mod and response_text and not response_text.startswith("【安全"):
            try:
                mem = memory_mod.HongjunMemory(user_id=session.platform_chat_id or session.platform)
                mem.remember(
                    content=f"用户：{message}\n鸿钧：{response_text[:500]}",
                    importance=0.6,
                    tags=["对话"],
                )
            except Exception as e:
                logger.warning(f"memory store error: {e}")

        # ── 步骤7：过滤标签 + 存储 ────────────────────────────
        response_text = _clean_response(response_text)
        session.add_message("assistant", response_text)
        session.set_state(SessionState.IDLE)

        # 检查是否需要 compaction
        compact_count = 0
        if session.should_compact():
            compact_count = session.compact()

        return {
            "session_id": session.id,
            "response": response_text,
            "state": session.state.value,
            "message_count": session.message_count,
            "compacted": compact_count,
            "eval_score": eval_report.overall_score if eval_report else None,
        }

    # ── HTTP 路由 ─────────────────────────────────────────────────

    async def _handle_chat(self, request: web.Request) -> web.Response:
        """POST /chat"""
        trace_id = str(uuid.uuid4())[:8]

        try:
            body = await request.json()
        except Exception:
            _trace_response(logger, "_handle_chat", trace_id, status=400, error="Invalid JSON body")
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400,
            )

        message: str = body.get("message", "").strip()
        if not message:
            _trace_response(logger, "_handle_chat", trace_id, status=400, error="Empty message")
            return web.json_response(
                {"error": "message is required"},
                status=400,
            )

        session_id: Optional[str] = body.get("session_id")
        model: str = body.get("model", "MiniMax-M2.7")
        platform: str = body.get("platform", "local")
        platform_chat_id: Optional[str] = body.get("platform_chat_id")

        _trace_request(logger, "_handle_chat", trace_id, session_id=session_id, model=model, platform=platform)

        # 获取或创建会话
        session = self.sessions.get_or_create_session(
            session_id=session_id,
            platform=platform,
            platform_chat_id=platform_chat_id,
            model=model,
        )

        if session.is_done():
            _trace_response(logger, "_handle_chat", trace_id, status=410, session_id=session.id, error="Session DONE")
            return web.json_response(
                {
                    "error": "Session is DONE. Create a new session.",
                    "session_id": session.id,
                },
                status=410,
            )

        start = time.time()
        self._request_count += 1

        try:
            result = await self._process_chat(session, message)
        except Exception as e:
            _trace_response(logger, "_handle_chat", trace_id, status=500, error=str(e))
            return web.json_response(
                {"error": str(e)},
                status=500,
            )

        latency = time.time() - start
        self._total_latency += latency
        result["latency_s"] = round(latency, 3)
        _trace_response(logger, "_handle_chat", trace_id, status=200, latency_s=latency, session_id=session.id)

        return web.json_response(result)

    async def _handle_stream(self, request: web.Request) -> web.Response:
        """POST /stream — SSE 流式聊天端点

        Body: {"message": "...", "session_id": "...", "model": "MiniMax-M2.7"}
        Returns: text/event-stream (SSE)
        """
        trace_id = str(uuid.uuid4())[:8]
        # ── 解析请求 ────────────────────────────────────────────
        try:
            body = await request.json()
        except Exception:
            _trace_response(logger, "_handle_stream", trace_id, status=400, error="Invalid JSON body")
            return web.json_response({"error": "Invalid JSON body"}, status=400)

        message: str = body.get("message", "").strip()
        if not message:
            _trace_response(logger, "_handle_stream", trace_id, status=400, error="Empty message")
            return web.json_response({"error": "message is required"}, status=400)

        session_id: Optional[str] = body.get("session_id")
        model: str = body.get("model", "MiniMax-M2.7")
        platform: str = body.get("platform", "local")
        platform_chat_id: Optional[str] = body.get("platform_chat_id")
        memory_context: str = body.get("memory_context", "")

        session = self.sessions.get_or_create_session(
            session_id=session_id,
            platform=platform,
            platform_chat_id=platform_chat_id,
            model=model,
        )

        _trace_request(logger, "_handle_stream", trace_id, session_id=session.id, model=model, platform=platform)

        if session.is_done():
            _trace_response(logger, "_handle_stream", trace_id, status=410, session_id=session.id, error="Session DONE")
            resp = web.StreamResponse(
                status=410,
                reason="Session DONE",
                headers={"Content-Type": "text/event-stream"},
            )
            await resp.prepare(request)
            await resp.write(b"data: {\"type\":\"error\",\"content\":\"Session DONE\"}\n\n")
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp

        session.set_state(SessionState.ACTIVE)
        session.add_message("user", message)

        # ── 构造 SSE 响应 ───────────────────────────────────────
        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "X-Accel-Buffering": "no",  # 禁用 nginx buffering
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)
        logger.debug("stream_started", handler="_handle_stream", trace_id=trace_id, session_id=session.id)

        def sse(data: dict):
            """发送一个 SSE 事件"""
            import json as _json
            return resp.write(f"data: {_json.dumps(data)}\n\n".encode())

        # ── 路由判断 ───────────────────────────────────────────
        # 简单对话：直接流 LLM token
        # 复杂任务：流式返回编排器结果（分 chunk 发送）
        if not HongjunGateway._needs_orchestrator(message):
            # ── 简单对话 → LLM token 流 ───────────────────────
            try:
                llm_mod = _get_llm()
                if llm_mod is None:
                    await sse({"type": "error", "content": "LLM 未就绪"})
                    await resp.write_eof()
                    return resp

                from ..llm import stream as llm_stream

                full_message = message
                if memory_context:
                    full_message = f"{memory_context}\n\n用户最新请求：{message}"

                accumulated = ""
                async for event in llm_stream(
                    messages=[{"role": "user", "content": full_message}],
                    model=session.model or model,
                    temperature=0.3,
                    max_tokens=4096,
                    chunk_size=15,
                    chunk_interval=0.0,
                ):
                    etype = event.get("type")
                    if etype == "chunk":
                        chunk = event["content"]
                        # 只发送新增部分
                        new_text = chunk[len(accumulated):]
                        if new_text:
                            accumulated = chunk
                            await sse({"type": "chunk", "content": new_text})
                    elif etype == "error":
                        await sse({"type": "error", "content": event["content"]})
                    elif etype == "done":
                        await sse({
                            "type": "done",
                            "content": accumulated,
                            "session_id": session.id,
                            "usage": event.get("usage", {}),
                        })

            except Exception as e:
                _trace_response(logger, "_handle_stream", trace_id, status=500, error=str(e))
                logger.exception(f"stream LLM error: {e}")
                await sse({"type": "error", "content": str(e)})

        else:
            # ── 复杂任务 → 编排器结果分 chunk 流 ───────────────
            await sse({"type": "status", "content": "🔄 正在分析意图..."})

            # ── 预审批检查（危险操作需用户确认）──────────────
            approval_pending: Optional[PendingApproval] = None
            for pattern, reason, severity in DANGEROUS_PATTERNS:
                m = re.search(pattern, message, re.IGNORECASE)
                if m:
                    # 提取实际命令片段用于展示
                    cmd_start = max(0, m.start() - 20)
                    cmd_fragment = message[cmd_start:m.end() + 20]
                    approval_pending = PendingApproval(
                        approval_id=str(uuid.uuid4())[:8],
                        operation="shell_command",
                        command=message,
                        reason=reason,
                        severity=severity,
                    )
                    await self.approvals.register(approval_pending)
                    await sse({
                        "type": "pending_approval",
                        "approval_id": approval_pending.id,
                        "operation": approval_pending.operation,
                        "command": message,
                        "reason": approval_pending.reason,
                        "severity": approval_pending.severity,
                        "timeout_seconds": APPROVAL_TIMEOUT_SEC,
                    })
                    # 等待用户审批（最多 120 秒）
                    try:
                        decision = await asyncio.wait_for(
                            approval_pending.future, timeout=APPROVAL_TIMEOUT_SEC
                        )
                    except asyncio.TimeoutError:
                        decision = "timeout"
                    if decision != "approved":
                        await self.approvals.resolve(approval_pending.id, "")
                        await sse({
                            "type": "done",
                            "content": f"❌ 操作已拒绝（或超时）：{approval_pending.reason}",
                            "session_id": session.id,
                        })
                        await resp.write_eof()
                        return resp
                    # approved → 继续执行
                    await sse({"type": "status", "content": "✅ 已确认，开始执行..."})
                    break

            # ── 两阶段执行：危险操作需要审批 ──────────────────────────
            # 第一阶段：编排器返回哨兵（危险操作未执行）
            # 第二阶段：审批后二次调用（危险操作已批准，直接执行）
            MAX_APPROVAL_ROUNDS = 3
            result: str = ""
            for _round in range(MAX_APPROVAL_ROUNDS):
                try:
                    loop = asyncio.get_event_loop()

                    def run_orchestrator():
                        # 第一次：approved_op=None（检查危险操作）
                        # 后续：approved_op=id（跳过检查，直接执行）
                        return HongjunGateway._call_orchestrator_impl(
                            message,
                            platform,
                            platform_chat_id,
                            approved_op=_round > 0 and _pending_approval_id or None,
                        )

                    result = await loop.run_in_executor(None, run_orchestrator)

                    # 检测哨兵：编排器告知需要审批
                    if isinstance(result, dict) and result.get("__NEEDS_APPROVAL__"):
                        pending = result
                        op_id = pending["approval_id"]
                        _pending_approval_id = op_id  # 暴露给 run_orchestrator 闭包
                        _pending_reason = pending.get("reason", "")
                        _pending_severity = pending.get("severity", 5)
                        _pending_command = pending.get("command", message)

                        # 写入已批准缓存（二次调用时编排器查此集合跳过检查）
                        _APPROVED_OPS[op_id] = True

                        await sse({
                            "type": "pending_approval",
                            "approval_id": op_id,
                            "operation": pending.get("operation", "unknown"),
                            "command": _pending_command,
                            "reason": _pending_reason,
                            "severity": _pending_severity,
                            "timeout_seconds": APPROVAL_TIMEOUT_SEC,
                        })

                        # 等待审批（最多 120 秒）
                        p = await self.approvals.get(op_id)
                        if p and not p.future.done():
                            try:
                                decision = await asyncio.wait_for(
                                    p.future, timeout=APPROVAL_TIMEOUT_SEC
                                )
                            except asyncio.TimeoutError:
                                decision = "timeout"
                        else:
                            decision = p.result if p else "timeout"

                        if decision != "approved":
                            _APPROVED_OPS.pop(op_id, None)
                            await self.approvals.resolve(op_id, "")
                            await sse({
                                "type": "done",
                                "content": f"❌ 操作已拒绝（或超时）：{_pending_reason}",
                                "session_id": session.id,
                            })
                            await resp.write_eof()
                            return resp

                        # approved → 继续下一轮（二次调用编排器）
                        await sse({"type": "status", "content": "✅ 已确认，执行中..."})
                        continue  # 下一轮：approved_op=op_id，编排器将直接执行

                    # 无哨兵，正常流程
                    break

                except Exception as e:
                    _trace_response(logger, "_handle_stream", trace_id, status=500, error=str(e))
                    logger.exception(f"stream orchestrator error: {e}")
                    await sse({"type": "error", "content": str(e)})
                    break

            # 清理待审批标记
            _pending_approval_id = None

            # 把结果分成多个 chunk 发送，模拟流式效果
            await sse({"type": "status", "content": "✅ 执行完成，正在发送结果..."})
            chunk_size = 80
            for i in range(0, len(result), chunk_size):
                await sse({"type": "chunk", "content": result[i:i + chunk_size]})
                await asyncio.sleep(0.01)  # 小延迟让前端能跟上

            await sse({
                "type": "done",
                "content": result,
                "session_id": session.id,
            })

        # ── 收尾 ──────────────────────────────────────────────
        session.add_message("assistant", accumulated if not HongjunGateway._needs_orchestrator(message) else result)
        session.set_state(SessionState.IDLE)

        if session.should_compact():
            session.compact()

        logger.debug("stream_ended", handler="_handle_stream", trace_id=trace_id, session_id=session.id)
        await resp.write_eof()
        return resp

    async def _handle_status(self, request: web.Request) -> web.Response:
        """GET /status"""
        queue_status = self.queue.get_status()
        active_sessions = [
            s.id for s in self.sessions.list_sessions()
            if s.state in (SessionState.ACTIVE, SessionState.IDLE)
        ]
        return web.json_response({
            "status": "running",
            "version": "0.1.0",
            "uptime": self.started_at,
            "port": self.port,
            "queue": queue_status,
            "active_sessions": active_sessions,
            "total_requests": self._request_count,
            "avg_latency_s": (
                round(self._total_latency / self._request_count, 3)
                if self._request_count > 0 else 0
            ),
        })

    async def _handle_list_sessions(self, request: web.Request) -> web.Response:
        """GET /sessions"""
        platform = request.query.get("platform")
        sessions = self.sessions.list_sessions(platform=platform)
        return web.json_response({
            "sessions": [
                {
                    "id": s.id,
                    "platform": s.platform,
                    "platform_chat_id": s.platform_chat_id,
                    "state": s.state.value,
                    "model": s.model,
                    "message_count": s.message_count,
                    "created_at": s.created_at,
                    "last_active_at": s.last_active_at,
                }
                for s in sessions
            ],
        })

    async def _handle_get_session(self, request: web.Request) -> web.Response:
        """GET /sessions/{id}"""
        session_id = request.match_info["id"]
        session = self.sessions.get_session(session_id)
        if not session:
            return web.json_response(
                {"error": "Session not found"},
                status=404,
            )
        messages = session.get_messages()
        return web.json_response({
            "id": session.id,
            "platform": session.platform,
            "platform_chat_id": session.platform_chat_id,
            "state": session.state.value,
            "model": session.model,
            "message_count": session.message_count,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "last_active_at": session.last_active_at,
            "messages": [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "timestamp": m["timestamp"],
                }
                for m in messages
            ],
        })

    async def _handle_shutdown_session(
        self, request: web.Request
    ) -> web.Response:
        """POST /sessions/{id}/shutdown"""
        session_id = request.match_info["id"]
        session = self.sessions.get_session(session_id)
        if not session:
            return web.json_response(
                {"error": "Session not found"},
                status=404,
            )
        self.sessions.destroy_session(session_id)
        return web.json_response({"ok": True, "session_id": session_id})

    async def _handle_shutdown(self, request: web.Request) -> web.Response:
        """POST /shutdown"""
        logger.info("Shutdown requested")
        # 延迟关闭，让响应先发出
        asyncio.get_event_loop().call_later(1, self._do_shutdown)
        return web.json_response({"ok": True, "message": "Shutting down"})

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """GET /metrics"""
        sessions = self.sessions.list_sessions()
        state_counts: dict[str, int] = {}
        for s in sessions:
            state_counts[s.state.value] = state_counts.get(s.state.value, 0) + 1
        return web.json_response({
            "request_count": self._request_count,
            "avg_latency_s": (
                round(self._total_latency / self._request_count, 3)
                if self._request_count > 0 else 0
            ),
            "queue": self.queue.get_status(),
            "sessions_by_state": state_counts,
            "total_sessions": len(sessions),
        })

    async def _handle_health(self, request: web.Request) -> web.Response:
        """GET /health - 简洁健康检查（给 systemd 用）"""
        return web.Response(text="OK")

    # ── 审批 Handlers ──────────────────────────────────────────────

    async def _handle_list_approvals(self, request: web.Request) -> web.Response:
        """GET /approvals — 列出所有待审批操作"""
        pending = await self.approvals.list_pending()
        return web.json_response({"pending": pending, "count": len(pending)})

    async def _handle_approve(self, request: web.Request) -> web.Response:
        """POST /approve/{approval_id}"""
        approval_id = request.match_info["approval_id"]
        ok = await self.approvals.approve(approval_id)
        if not ok:
            return web.json_response({"error": "approval not found or already resolved"}, status=404)
        return web.json_response({"status": "approved", "id": approval_id})

    async def _handle_reject(self, request: web.Request) -> web.Response:
        """POST /reject/{approval_id}"""
        approval_id = request.match_info["approval_id"]
        ok = await self.approvals.reject(approval_id)
        if not ok:
            return web.json_response({"error": "approval not found or already resolved"}, status=404)
        return web.json_response({"status": "rejected", "id": approval_id})

    # ── Cron Handlers ────────────────────────────────────────────

    async def _handle_cron_list(self, request: web.Request) -> web.Response:
        """GET /cron/jobs"""
        if not self.cron:
            return web.json_response({"error": "cron not available"}, status=500)
        jobs = self.cron.list_jobs()
        return web.json_response({"jobs": [j.to_dict() for j in jobs]})

    async def _handle_cron_create(self, request: web.Request) -> web.Response:
        """POST /cron/jobs"""
        if not self.cron:
            return web.json_response({"error": "cron not available"}, status=500)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        try:
            job = self.cron.create_job(
                name=body["name"],
                schedule_type=body.get("schedule_type", "cron"),
                schedule_value=body.get("schedule_value", "*/5 * * * *"),
                target_type=body["target_type"],
                target_id=body["target_id"],
                target_message=body["target_message"],
                description=body.get("description", ""),
                priority=body.get("priority", "normal"),
                max_retries=body.get("max_retries", 3),
                enabled=body.get("enabled", True),
            )
            return web.json_response(job.to_dict(), status=201)
        except (KeyError, ValueError) as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_cron_get(self, request: web.Request) -> web.Response:
        """GET /cron/jobs/{id}"""
        if not self.cron:
            return web.json_response({"error": "cron not available"}, status=500)
        job_id = request.match_info["id"]
        job = self.cron.get_job(job_id)
        if not job:
            return web.json_response({"error": "job not found"}, status=404)
        return web.json_response(job.to_dict())

    async def _handle_cron_delete(self, request: web.Request) -> web.Response:
        """DELETE /cron/jobs/{id}"""
        if not self.cron:
            return web.json_response({"error": "cron not available"}, status=500)
        job_id = request.match_info["id"]
        ok = self.cron.delete_job(job_id)
        if not ok:
            return web.json_response({"error": "job not found"}, status=404)
        return web.json_response({"deleted": True})

    async def _handle_cron_trigger(self, request: web.Request) -> web.Response:
        """POST /cron/jobs/{id}/trigger"""
        if not self.cron:
            return web.json_response({"error": "cron not available"}, status=500)
        job_id = request.match_info["id"]
        ok = self.cron.trigger_job(job_id)
        if not ok:
            return web.json_response({"error": "job not found"}, status=404)
        return web.json_response({"triggered": True})

    async def _handle_cron_enable(self, request: web.Request) -> web.Response:
        """POST /cron/jobs/{id}/enable"""
        if not self.cron:
            return web.json_response({"error": "cron not available"}, status=500)
        job_id = request.match_info["id"]
        job = self.cron.enable_job(job_id)
        if not job:
            return web.json_response({"error": "job not found"}, status=404)
        return web.json_response(job.to_dict())

    async def _handle_cron_disable(self, request: web.Request) -> web.Response:
        """POST /cron/jobs/{id}/disable"""
        if not self.cron:
            return web.json_response({"error": "cron not available"}, status=500)
        job_id = request.match_info["id"]
        job = self.cron.disable_job(job_id)
        if not job:
            return web.json_response({"error": "job not found"}, status=404)
        return web.json_response(job.to_dict())

    async def _handle_cron_execute(self, request: web.Request) -> web.Response:
        """POST /cron/execute — Cron 调度器触发任务执行

        Body: {"message": "任务描述", "platform": "feishu", "platform_chat_id": "oc_xxx"}
        """
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        message: str = body.get("message", "").strip()
        if not message:
            return web.json_response({"error": "message required"}, status=400)

        platform = body.get("platform", "cron")
        platform_chat_id = body.get("platform_chat_id")

        session = self.sessions.get_or_create_session(
            session_id=None,
            platform=platform,
            platform_chat_id=platform_chat_id,
            model="MiniMax-M2.7",
        )

        try:
            result = await self._process_chat(session, message)
            return web.json_response({
                "ok": True,
                "response": result.get("response", ""),
                "session_id": session.id,
            })
        except Exception as e:
            logger.exception(f"Cron execute error: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_cron_status(self, request: web.Request) -> web.Response:
        """GET /cron/status"""
        if not self.cron:
            return web.json_response({"error": "cron not available"}, status=500)
        return web.json_response(self.cron.status())

    # ── Agent Handlers ──────────────────────────────────────────────

    async def _handle_agent_chat(self, request: web.Request) -> web.Response:
        """POST /agent/chat — 使用新的 FunctionCallAgent 处理

        Body: {
            "message": "用户消息",
            "function_list": ["shell", "file_read"],   // 可选，默认 ["shell"]
            "system_message": "你是...",               // 可选
            "stream": false,                           // 可选，默认 false
        }
        """
        trace_id = str(uuid.uuid4())[:8]
        try:
            body = await request.json()
        except Exception:
            _trace_response(logger, "_handle_agent_chat", trace_id, status=400, error="Invalid JSON")
            return web.json_response({"error": "Invalid JSON"}, status=400)

        message: str = body.get("message", "").strip()
        if not message:
            _trace_response(logger, "_handle_agent_chat", trace_id, status=400, error="Empty message")
            return web.json_response({"error": "message required"}, status=400)

        function_list = body.get("function_list", ["shell"])
        system_message = body.get(
            "system_message",
            "你是鸿钧 AI Agent，善于使用工具完成任务。"
        )
        stream = body.get("stream", False)

        _trace_request(logger, "_handle_agent_chat", trace_id, function_list=function_list, stream=stream)

        # 延迟导入避免循环依赖
        from ..agent import FunctionCallAgent

        try:
            agent = FunctionCallAgent(
                name="鸿钧",
                function_list=function_list,
                system_message=system_message,
            )

            messages = [{"role": "user", "content": message}]
            responses = list(agent.run(messages, stream=False))
            final_response = responses[-1][-1].content if responses else ""

            _trace_response(logger, "_handle_agent_chat", trace_id, status=200)
            return web.json_response({
                "ok": True,
                "response": final_response,
                "agent": agent.name,
                "tools_used": [t["function"]["name"] for t in agent.get_functions()],
            })

        except Exception as e:
            _trace_response(logger, "_handle_agent_chat", trace_id, status=500, error=str(e))
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_agent_functions(self, request: web.Request) -> web.Response:
        """GET /agent/functions — 获取可用工具列表（OpenAI schema）"""
        trace_id = str(uuid.uuid4())[:8]
        _trace_request(logger, "_handle_agent_functions", trace_id)
        try:
            from ..agent import FunctionCallAgent
            from ..tools import init_skills, TOOL_REGISTRY

            init_skills()

            # 返回全局 TOOL_REGISTRY 中的所有工具
            schemas = TOOL_REGISTRY.get_openai_functions()
            _trace_response(logger, "_handle_agent_functions", trace_id, status=200, total=len(schemas))
            return web.json_response({
                "functions": schemas,
                "total": len(schemas),
            })
        except Exception as e:
            _trace_response(logger, "_handle_agent_functions", trace_id, status=500, error=str(e))
            return web.json_response({"error": str(e)}, status=500)

    def _setup_routes(self):
        """注册所有 HTTP 路由"""
        r = self.app.router
        r.add_post("/chat", self._handle_chat)
        r.add_post("/stream", self._handle_stream)   # SSE 流式聊天
        r.add_post("/agent/chat", self._handle_agent_chat)
        r.add_get("/agent/functions", self._handle_agent_functions)
        r.add_get("/status", self._handle_status)
        r.add_get("/sessions", self._handle_list_sessions)
        r.add_get(r"/sessions/{id}", self._handle_get_session)
        r.add_post(r"/sessions/{id}/shutdown", self._handle_shutdown_session)
        r.add_post("/shutdown", self._handle_shutdown)
        r.add_get("/metrics", self._handle_metrics)
        r.add_get("/health", self._handle_health)

        # 审批路由
        r.add_get("/approvals", self._handle_list_approvals)
        r.add_post("/approve/{approval_id}", self._handle_approve)
        r.add_post("/reject/{approval_id}", self._handle_reject)

        # Cron 路由
        if self.cron:
            r.add_get("/cron/jobs", self._handle_cron_list)
            r.add_post("/cron/jobs", self._handle_cron_create)
            r.add_get(r"/cron/jobs/{id}", self._handle_cron_get)
            r.add_delete(r"/cron/jobs/{id}", self._handle_cron_delete)
            r.add_post(r"/cron/jobs/{id}/trigger", self._handle_cron_trigger)
            r.add_post(r"/cron/jobs/{id}/enable", self._handle_cron_enable)
            r.add_post(r"/cron/jobs/{id}/disable", self._handle_cron_disable)
            r.add_get("/cron/status", self._handle_cron_status)
            r.add_post("/cron/execute", self._handle_cron_execute)

    # ── 生命周期 ─────────────────────────────────────────────────

    def _ensure_skills_loaded(self):
        """在启动时强制加载 SkillManager（独立 Agent 原则）"""
        try:
            from hongjun.skill_manager import SKILL_MANAGER
            # discover() 在模块级已调用，此处仅验证
            count = len(SKILL_MANAGER.skills)
            logger.info(f"   🛠️  SkillManager 已加载 {count} 个 skills: {list(SKILL_MANAGER.skills.keys())}")
        except Exception as e:
            logger.warning(f"   ⚠️ SkillManager 加载失败: {e}")

    async def start(self):
        """启动 Gateway"""
        global _gateway_instance
        _gateway_instance = self

        # 预加载 SkillManager（独立 Agent 原则：skills 在鸿钧自己目录下）
        self._ensure_skills_loaded()

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        logger.info(
            f"🚀 鸿钧 Gateway 启动成功 → http://{self.host}:{self.port}"
        )
        logger.info(f"   POST /chat          - 聊天")
        logger.info(f"   GET  /status        - 状态")
        logger.info(f"   GET  /sessions      - 会话列表")
        logger.info(f"   GET  /sessions/{{id}} - 会话详情")
        logger.info(f"   POST /shutdown      - 关闭 Gateway")

        # 启动飞书通道（WebSocket 实时事件）
        await _start_feishu(self)

        # 启动 Cron 调度器
        if self.cron:
            self.cron.start()
            logger.info("cron_scheduler_started", job_count=len(self.cron.list_jobs()))

        # 启动 MCP Server（HTTP Streamable）
        await _start_mcp(self)

    def _do_shutdown(self):
        """执行关闭"""
        logger.info("Gateway shutting down...")
        # 先停止飞书通道
        asyncio.get_event_loop().run_until_complete(_stop_feishu())
        # 停止 MCP Server
        asyncio.get_event_loop().run_until_complete(_stop_mcp())
        # 停止 Cron 调度器
        if self.cron:
            self.cron.stop()
        if self.loop:
            self.loop.stop()

    async def wait(self):
        """阻塞直到 shutdown"""
        self.loop = asyncio.get_event_loop()
        shutdown_event = asyncio.Event()

        def signal_handler():
            logger.info("Received signal, shutting down...")
            shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            self.loop.add_signal_handler(sig, signal_handler)

        await shutdown_event.wait()


# ── 快捷启动函数 ──────────────────────────────────────────────────

def create_app() -> HongjunGateway:
    """创建 Gateway 实例（供 uvicorn 使用）"""
    return HongjunGateway()


async def start_gateway(
    host: str = "0.0.0.0",
    port: int = 20830,
    log_level: str = "INFO",
):
    """启动 Gateway（直接运行用）"""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    gateway = HongjunGateway(host=host, port=port)
    await gateway.start()
    await gateway.wait()


# ── uvicorn 入口点 ────────────────────────────────────────────────

app = HongjunGateway().app  # 供 `uvicorn gateway.server:app` 使用
