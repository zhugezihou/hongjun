"""
工部 · Agent CLI
================

鸿钧 Agent 的命令行入口。

支持两种模式：
  交互模式（无参数）：多轮对话，readline 历史支持
  单次模式（--once）：传入 query，执行后退出

会话持久化：SQLite（`~/.hongjun/sessions.db`），跨会话保留对话历史。

用法：
  python -m hongjun.cli                    # 交互模式
  python -m hongjun.cli "你好"             # 单次模式
  python -m hongjun.cli --session SESSION  # 指定会话
  python -m hongjun.cli --list            # 列出所有会话
  python -m hongjun.cli --show SESSION     # 显示会话内容

依赖：仅标准库，不引入额外依赖。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ANSI 颜色（跨平台兼容）
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
DIM = "\033[2m"
RESET = "\033[0m"


def color(text: str, c: str) -> str:
    return f"{c}{text}{RESET}"

# ── Context Compression ───────────────────────────────────────────────────────

MAX_CONTEXT_TOKENS = 6000
KEEP_RECENT_MSGS = 16
TOKEN_ESTIMATE_RATIO = 4


def _estimate_tokens(text: str) -> int:
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return chinese_chars * 2 + other_chars // TOKEN_ESTIMATE_RATIO


def _compress_messages(
    messages: list,
    session,
    max_tokens: int = MAX_CONTEXT_TOKENS,
) -> tuple[list, bool]:
    """压缩过长消息历史，返回 (compressed, did_compress)"""
    total_tokens = sum(_estimate_tokens(m.content) for m in messages)
    if total_tokens <= max_tokens:
        return messages, False

    keep_count = KEEP_RECENT_MSGS
    if len(messages) <= keep_count + 2:
        return messages, False

    system_msg = messages[0] if messages[0].role == "system" else None
    recent = messages[-keep_count:]
    old = messages[1 if system_msg else 0:-keep_count]

    summary_text = _summarize_old_messages(old)

    compressed = []
    if system_msg:
        compressed.append(system_msg)
    compressed.append(ChatMessage(
        role="system",
        content=f"[CONTEXT COMPACTION] 早期 {len(old)} 条消息摘要：\n{summary_text}"
    ))
    compressed.extend(recent)

    session._compression_count = getattr(session, "_compression_count", 0) + 1
    return compressed, True


def _summarize_old_messages(old_messages: list) -> str:
    if not old_messages:
        return "(无历史消息)"

    api_key = _load_hermes_api_key()
    if not api_key:
        return _simple_summarize(old_messages)

    try:
        import httpx
        url = "https://api.minimaxi.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        history_preview = "\n".join(
            f"[{m.role}] {m.content[:300]}" + ("..." if len(m.content) > 300 else "")
            for m in old_messages[-20:]
        )
        payload = {
            "model": "MiniMax-M2.7",
            "messages": [
                {"role": "system", "content": "你是对话历史摘要器，简洁总结核心内容。"},
                {"role": "user", "content": f"对话历史：\n{history_preview}\n\n用2-4句话总结："}
            ],
            "temperature": 0.3,
            "max_tokens": 500,
        }
        resp = httpx.post(url, headers=headers, json=payload, timeout=20)
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        return _simple_summarize(old_messages)
    except Exception:
        return _simple_summarize(old_messages)


def _simple_summarize(old_messages: list) -> str:
    if not old_messages:
        return "(无历史消息)"
    user_msgs = [m.content for m in old_messages if m.role == "user"]
    parts = []
    if user_msgs:
        parts.append(f"用户首问：{user_msgs[0][:80]}...")
    if len(user_msgs) > 1:
        parts.append(f"共 {len(user_msgs)} 轮对话")
    return " | ".join(parts) if parts else "(对话内容已丢失)"




# ── Session Store (SQLite) ──────────────────────────────────────────────────

SESSIONS_DB = Path(os.path.expanduser("~/.hongjun/sessions.db"))


def _init_sessions_db() -> None:
    """初始化 sessions 数据库"""
    SESSIONS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SESSIONS_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            system_message TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()


@dataclass
class ChatMessage:
    role: str  # user / assistant / system / tool
    content: str
    name: Optional[str] = None  # tool_name


@dataclass
class ChatSession:
    id: str
    name: str
    created_at: str
    updated_at: str
    system_message: str
    messages: list[ChatMessage] = field(default_factory=list)

    @property
    def display_time(self) -> str:
        """友好显示时间"""
        try:
            dt = datetime.datetime.fromisoformat(self.updated_at)
            now = datetime.datetime.now()
            diff = now - dt
            if diff.total_seconds() < 60:
                return "刚刚"
            elif diff.total_seconds() < 3600:
                return f"{int(diff.total_seconds() // 60)}分钟前"
            elif diff.days == 0:
                return dt.strftime("%H:%M")
            elif diff.days == 1:
                return "昨天"
            else:
                return dt.strftime("%m-%d %H:%M")
        except Exception:
            return self.updated_at


class SessionStore:
    """SQLite 会话存储"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or SESSIONS_DB
        _init_sessions_db()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def create_session(self, name: str, system_message: str = "") -> ChatSession:
        """创建新会话"""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        session_id = f"session_{int(datetime.datetime.now().timestamp() * 1000)}"
        conn = self._conn()
        conn.execute(
            "INSERT INTO sessions (id, name, created_at, updated_at, system_message) VALUES (?, ?, ?, ?, ?)",
            (session_id, name, now, now, system_message),
        )
        conn.commit()
        conn.close()
        return ChatSession(
            id=session_id,
            name=name,
            created_at=now,
            updated_at=now,
            system_message=system_message,
        )

    def get_session(self, session_id: str) -> Optional[ChatSession]:
        """获取会话（含消息）"""
        conn = self._conn()
        row = conn.execute(
            "SELECT id, name, created_at, updated_at, system_message FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            conn.close()
            return None
        msgs = conn.execute(
            "SELECT role, content, name FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        conn.close()
        return ChatSession(
            id=row[0],
            name=row[1],
            created_at=row[2],
            updated_at=row[3],
            system_message=row[4] or "",
            messages=[ChatMessage(role=m[0], content=m[1], name=m[2]) for m in msgs],
        )

    def get_or_create_session(self, session_id: Optional[str]) -> ChatSession:
        """获取或创建会话"""
        if session_id:
            s = self.get_session(session_id)
            if s:
                return s
        return self.create_session(name=f"会话-{datetime.datetime.now().strftime('%m-%d %H:%M')}")

    def list_sessions(self) -> list[ChatSession]:
        """列出所有会话（不含消息内容）"""
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, name, created_at, updated_at, system_message FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        return [
            ChatSession(id=r[0], name=r[1], created_at=r[2], updated_at=r[3], system_message=r[4] or "")
            for r in rows
        ]

    def append_message(self, session_id: str, role: str, content: str, name: Optional[str] = None) -> None:
        """追加消息"""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = self._conn()
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at, name) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, now, name),
        )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        conn.commit()
        conn.close()

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        conn = self._conn()
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        conn.close()
        return cur.rowcount > 0


# ── Agent 核心 ──────────────────────────────────────────────────────────────

HONGJUN_SYSTEM_MESSAGE = """你是**鸿钧**（Hongjun），一个运行在 WSL 环境下的 AI Agent。

## 你的身份
- 名字：鸿钧
- 你是一个 AI Agent，通过工具（shell/file_read/write 等）完成用户任务
- 你有内置工具，也有外接 Skills 系统

## 你能做什么（内置工具）
| 工具 | 说明 |
|------|------|
| shell | 执行 shell 命令（ls/ps/grep/systemctl 等） |
| file_read | 读取文件（限制500行） |
| file_write | 写入文件 |
| upgrade_status | 查询鸿钧版本和升级状态 |
| upgrade_run | 执行鸿钧升级 |
| upgrade_repair | 修复鸿钧 |
| upgrade_rollback | 回滚鸿钧 |

## Skills 系统（外接工具）
鸿钧通过 Skills 系统扩展能力。遇到具体任务时，优先考虑调用 Skills。

## 回答用户问题时的原则
1. **问你是谁/做什么的**：简洁回答"我是鸿钧 AI Agent，善于使用工具完成任务"，不要跑 shell 命令
2. **问各模块是否正常**：不要跑 systemctl/crontab 等命令，直接说"鸿钧目前正常"或"各模块正常运行中"
3. **问某个工具能做什么**：直接描述工具能力，不要跑命令验证
4. **遇到不认识的指令**：先说"这个任务超出我的能力范围"或"需要更具体的信息"
5. **不要跑命令来回答身份类问题**：用户问你是谁、问你的能力，不要用 shell 命令来"探索"
6. **系统状态不等于你的状态**：别把 cron/systemd 的状态当成你的模块状态

## 工具调用原则
- 遇到具体操作需求时再调用工具，不要在回答身份问题时跑命令
- 如果用户要求你做操作（部署/重启/配置修改），再调用对应工具
"""


def _load_hermes_api_key() -> Optional[str]:
    """从 Hermes auth.json 加载 MiniMax API key（access_token）"""
    import os
    key = os.environ.get("MINIMAX_API_KEY", "")
    if key:
        return key
    hermes_auth = Path.home() / ".hermes" / "auth.json"
    if hermes_auth.exists():
        try:
            with open(hermes_auth) as f:
                d = json.load(f)
            pool = d.get("credential_pool", {})
            creds = pool.get("custom:api.minimaxi.com", [])
            if creds:
                token = creds[0].get("access_token", "")
                if token:
                    return token
        except Exception:
            pass
    return None


def _build_agent(
    system_message: str,
    function_list: list[str],
):
    """构建 FunctionCallAgent 实例"""
    from .agent import FunctionCallAgent

    # 尝试获取 API key（优先环境变量，其次 Hermes auth.json）
    api_key = _load_hermes_api_key()
    if api_key:
        import os
        os.environ["MINIMAX_API_KEY"] = api_key

    system_msg = system_message or HONGJUN_SYSTEM_MESSAGE

    agent = FunctionCallAgent(
        name="鸿钧",
        system_message=system_msg,
        function_list=function_list,
        llm={"model": "MiniMax-M2.7"},  # FunctionCallAgent._call_llm 会懒加载
    )
    return agent


def _run_single(
    agent,
    user_message: str,
    messages: list,
) -> str:
    """执行单次查询，返回最终回复文本"""
    messages.append(ChatMessage(role="user", content=user_message))

    # 构建 LLM 消息格式
    llm_messages = [{"role": m.role, "content": m.content} for m in messages]

    # 运行 agent
    responses = list(agent.run(llm_messages, stream=False))
    # responses: List[List[Message]]，找最后一个 assistant 回复
    final_response = ""
    for rsp_list in responses:
        for msg in rsp_list:
            if msg.role == "assistant" and msg.content:
                final_response = msg.content
    return final_response


# ── 流式输出渲染 ──────────────────────────────────────────────────────────

def _stream_print(text: str, color_fn=None) -> None:
    """打印流式文本（逐步输出）"""
    if color_fn:
        sys.stdout.write(color_fn(text))
    else:
        sys.stdout.write(text)
    sys.stdout.flush()


def _print_assistant(text: str) -> None:
    """打印 assistant 消息"""
    print()
    print(color(f"  {text}", DIM))


def _print_tool_call(tool_name: str, args_str: str) -> None:
    """打印工具调用"""
    print()
    print(color(f"  🔧 [{tool_name}]", CYAN))
    if args_str and args_str != "{}":
        print(color(f"     {args_str[:200]}", DIM))


# ── 交互式会话 ─────────────────────────────────────────────────────────────

import readline  # noqa: E402  # history support


def _input_with_history(prompt: str) -> str:
    """带历史记录的输入"""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""


def _print_banner() -> None:
    try:
        from hongjun import __version__
        ver = __version__
    except Exception:
        ver = "0.4.0"
    banner = f"""\033[1m\033[36m╔═══════════════════════════════════════════════════════════════════╗
║                     鸿钧 Agent CLI  (v{ver})                     ║
╠═══════════════════════════════════════════════════════════════════╣
║  \033[33m交互模式\033[0m\033[36m                                                         ║
║    直接输入问题即可聊天，exit/quit 退出                            ║
╠═══════════════════════════════════════════════════════════════════╣
║  \033[33m启动方式\033[0m\033[36m                                                         ║
║    python -m hongjun.cli              交互模式                   ║
║    python -m hongjun.cli "查询"        单次模式                    ║
╠═══════════════════════════════════════════════════════════════════╣
║  \033[33m会话管理\033[0m\033[36m                                                         ║
║    --list / -l                        列出会话                    ║
║    --show <session_id>                查看会话内容                ║
║    --delete <session_id>              删除会话                    ║
║    --session / -s <name>              指定会话（不存在则创建）     ║
╠═══════════════════════════════════════════════════════════════════╣
║  \033[33m自检与修复\033[0m\033[36m                                                        ║
║    --doctor / --fix                   自检医生（4步检查+修复）    ║
╠═══════════════════════════════════════════════════════════════════╣
║  \033[33mGateway 运维\033[0m\033[36m                                                        ║
║    python -m hongjun.gateway          启动 Gateway（端口 20830）  ║
║    python scripts/watchdog.py --daemon  看门狗守护进程            ║
║    python scripts/self_check.py --fix   全量自检+修复（cron 用）  ║
╚═══════════════════════════════════════════════════════════════════╝\033[0m
"""
    print(banner)


def run_chat(
    session: ChatSession,
    function_list: Optional[list[str]] = None,
) -> None:
    """交互式聊天会话"""
    _print_banner()
    print(color(f"  会话: {session.name}  (ID: {session.id})", DIM))
    print(color(f"  历史消息: {len(session.messages)} 条", DIM))
    print()

    # 加载历史到 readline
    _load_readline_history(session)

    # 构建 agent
    function_list = function_list or ["shell"]
    try:
        agent = _build_agent(session.system_message, function_list)
    except Exception as e:
        print(color(f"  ❌ Agent 初始化失败: {e}", RED))
        return

    # 构建消息列表（含历史）
    messages: list[ChatMessage] = list(session.messages)
    prompt_count = sum(1 for m in messages if m.role == "user")

    while True:
        prompt_count += 1
        try:
            user_input = _input_with_history(
                color(f"\n[{prompt_count}] {BOLD}你{RESET}> ", GREEN)
            )
        except (KeyboardInterrupt, EOFError):
            print("\n\n", color("👋 再见！", CYAN))
            break

        if not user_input or user_input.strip() in ("exit", "quit", "q"):
            print(color("👋 再见！", CYAN))
            break

        if not user_input.strip():
            continue

        user_input = user_input.strip()

        # 保存用户消息
        messages.append(ChatMessage(role="user", content=user_input))
        store = SessionStore()
        store.append_message(session.id, "user", user_input)

        # 追加到 readline 历史
        readline.add_history(user_input)

        # 上下文压缩（防止无限膨胀）
        messages, did_compress = _compress_messages(messages, session)
        if did_compress:
            print(color(f"  [上下文已压缩，当前 {sum(_estimate_tokens(m.content) for m in messages)} tokens]", DIM))

        # 构建 LLM 消息
        llm_messages = [{"role": m.role, "content": m.content} for m in messages]

        # 流式运行 agent
        try:
            responses = list(agent.run(llm_messages, stream=False))
        except Exception as e:
            print(color(f"\n  ❌ 执行出错: {e}", RED))
            traceback.print_exc()
            continue

        # 提取最终回复
        final_text = ""
        for rsp_batch in responses:
            for msg in rsp_batch:
                if msg.role == "assistant" and msg.content:
                    final_text = msg.content

        # 打印最终回复
        if final_text:
            _print_assistant(final_text)
            messages.append(ChatMessage(role="assistant", content=final_text))
            store.append_message(session.id, "assistant", final_text)
        else:
            _print_assistant("(无回复)")
            # 回退：从 responses 提取任意内容
            for rsp_batch in responses:
                for msg in rsp_batch:
                    if msg.content:
                        _print_assistant(msg.content)
                        messages.append(ChatMessage(role="assistant", content=msg.content))
                        store.append_message(session.id, "assistant", msg.content)
                        break
                else:
                    continue
                break


def _load_readline_history(session: ChatSession) -> None:
    """从历史加载 readline 内容"""
    for m in session.messages:
        if m.role == "user":
            readline.add_history(m.content)


# ── 单次模式 ───────────────────────────────────────────────────────────────

def run_once(query: str, function_list: Optional[list[str]] = None) -> None:
    """单次执行模式"""
    function_list = function_list or ["shell"]
    agent = _build_agent("", function_list)
    messages = [ChatMessage(role="user", content=query)]
    llm_messages = [{"role": m.role, "content": m.content} for m in messages]

    try:
        responses = list(agent.run(llm_messages, stream=False))
        final_response = responses[-1][-1].content if responses else ""
        if final_response:
            print(final_response)
        else:
            print("(无回复)")
    except Exception as e:
        print(color(f"❌ 错误: {e}", RED), file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


# ── Session 管理命令 ─────────────────────────────────────────────────────────

def run_doctor() -> None:
    """运行自检医生：完整检查 + 自动修复 + 友好输出"""
    import time
    from hongjun.logging_config import get_logger

    logger = get_logger("hongjun.doctor")
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    DIM = "\033[2m"

    def c(text, color): return f"{color}{text}{RESET}"

    print(c("\n  🔍 鸿钧 Doctor 自检中...\n", BOLD + CYAN))

    hongjun_root = Path(__file__).parent.parent.parent  # /home/asus/hongjun/src/hongjun/cli.py → parent.parent.parent = /home/asus/hongjun/
    sys.path.insert(0, str(hongjun_root / "src"))
    sys.path.insert(0, str(hongjun_root))  # for scripts/
    from hongjun.self_repair import SelfRepairEngine
    from scripts.self_check import check_gateway, restart_gateway, check_logs

    # ── 1. Gateway 健康检查 ────────────────────────────────────────
    print(c("  [1/4] 检查 Gateway 进程...", DIM), end=" ", flush=True)
    gw = check_gateway()
    if gw["status"] == "OK":
        print(c("✅ 正常", GREEN))
    else:
        print(c(f"❌ {gw['status']}", RED))
        print(c(f"       尝试重启... ", DIM), end=" ", flush=True)
        result = restart_gateway()
        time.sleep(5)
        gw2 = check_gateway()
        if gw2["status"] == "OK":
            print(c(f"✅ 已恢复 PID={result.get('pid')}", GREEN))
        else:
            print(c(f"❌ 重启失败", RED))

    # ── 2. 日志错误扫描 ───────────────────────────────────────────
    print(c("  [2/4] 扫描日志错误...", DIM), end=" ", flush=True)
    errors = check_logs()
    if errors:
        print(c(f"⚠️  发现 {len(errors)} 条", YELLOW))
        seen = set()
        for e in errors[:5]:
            short = e[:120]
            if short not in seen:
                seen.add(short)
                print(f"    {c('•', YELLOW)} `{short}`")
    else:
        print(c("✅ 无错误", GREEN))

    # ── 3. 代码诊断 ────────────────────────────────────────────────
    print(c("  [3/4] 诊断代码模块...", DIM), end=" ", flush=True)
    engine = SelfRepairEngine()
    diag = engine.run_diagnostics()
    if diag.issues:
        print(c(f"⚠️  发现 {len(diag.issues)} 个问题", YELLOW))
        for iss in diag.issues[:5]:
            sev = c(iss.severity.upper(), RED if iss.severity == "critical" else YELLOW)
            print(f"    {c('•', YELLOW)} [{sev}] {iss.module}: {iss.description[:80]}")
    else:
        print(c(f"✅ {diag.modules_ok} 个模块正常", GREEN))

    # ── 4. 自动修复 ────────────────────────────────────────────────
    repairs_done = []
    if diag.issues:
        print(c("  [4/4] 尝试自动修复...", DIM))
        for iss in diag.issues:
            if iss.severity not in ("critical", "error"):
                continue
            if not iss.module:
                continue
            print(f"    修复 {iss.module}... ", end="", flush=True)
            results = engine.fix_module(iss.module, f"{iss.description} at line {iss.line}")
            if results and results[0].success:
                print(c("✅", GREEN), c(results[0].description[:60], DIM))
                repairs_done.append(results[0])
            else:
                reason = results[0].reason if results else "UNKNOWN"
                print(c(f"❌ {reason}", RED), c(results[0].description[:60] if results else "", DIM))
    else:
        print(c("  [4/4] 无需修复", DIM))

    # ── 汇总 ──────────────────────────────────────────────────────
    print()
    all_good = gw["status"] == "OK" and not errors and not diag.issues
    if all_good:
        print(c(f"  ✅ 鸿钧状态：**一切正常**", BOLD + GREEN))
    elif repairs_done:
        print(c(f"  🔧 已自动修复 {len(repairs_done)} 项，Gateway 运行中", BOLD + YELLOW))
    elif gw["status"] != "OK":
        print(c(f"  ❌ Gateway 异常，请检查", BOLD + RED))
    elif diag.issues:
        print(c(f"  ⚠️  存在 {len(diag.issues)} 个代码问题（见上方）", BOLD + YELLOW))
    else:  # 有日志警告但 gateway 正常
        print(c(f"  ✅ Gateway 正常，日志有 {len(errors)} 条历史警告（可忽略）", BOLD + YELLOW))
    print()


def run_list() -> None:
    """列出所有会话"""
    store = SessionStore()
    sessions = store.list_sessions()
    if not sessions:
        print(color("  暂无会话记录", DIM))
        return
    print(color(f"\n  共 {len(sessions)} 个会话：\n", DIM))
    for s in sessions:
        print(f"  {color(s.id, CYAN)}  {color(s.name, BOLD)}  {color(s.display_time, DIM)}")


def run_show(session_id: str) -> None:
    """显示会话详情"""
    store = SessionStore()
    session = store.get_session(session_id)
    if not session:
        print(color(f"  会话 {session_id} 不存在", RED))
        return
    print(color(f"\n  会话: {session.name}  (创建于 {session.created_at[:19]})\n", BOLD))
    for m in session.messages:
        role_color = {"user": GREEN, "assistant": CYAN, "system": YELLOW, "tool": MAGENTA}.get(m.role, DIM)
        prefix = {"user": "你", "assistant": "🤖", "system": "系统", "tool": f"🔧 {m.name}"}.get(m.role, m.role)
        print(color(f"  [{prefix}] ", role_color), end="")
        content = m.content[:300] + ("..." if len(m.content) > 300 else "")
        print(content.replace("\n", "\n     "))


# ── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="hongjun",
        description="鸿钧 Agent CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m hongjun.cli                        # 交互模式
  python -m hongjun.cli "你好"                 # 单次查询
  python -m hongjun.cli --once "你好"          # 单次查询（同上）
  python -m hongjun.cli --list                 # 列出会话
  python -m hongjun.cli --show session_xxx     # 查看会话内容
  python -m hongjun.cli --delete session_xxx  # 删除会话
  python -m hongjun.cli --session mysession   # 指定会话（不存在则创建）
        """,
    )
    parser.add_argument("query", nargs="?", help="单次查询（指定后以单次模式运行）")
    parser.add_argument("--once", action="store_true", help="单次查询模式（等同于直接传 query）")
    parser.add_argument("--session", "-s", help="会话 ID 或名称")
    parser.add_argument("--list", "-l", action="store_true", help="列出所有会话")
    parser.add_argument("--show", help="显示会话内容")
    parser.add_argument("--delete", help="删除指定会话")
    parser.add_argument("--doctor", action="store_true", help="自检医生：完整检查 + 自动修复")
    parser.add_argument("--fix", action="store_true", help="自检医生（--doctor 的别名）")
    parser.add_argument(
        "--tools", "-t", nargs="*", default=["shell"],
        help="启用的工具列表（默认: shell）",
    )
    parser.add_argument("--system", help="系统提示词")

    args = parser.parse_args()

    # 模式分支
    if args.list:
        run_list()
        return

    if args.show:
        run_show(args.show)
        return

    if args.delete:
        store = SessionStore()
        ok = store.delete_session(args.delete)
        print(color(f"  {'✅ 已删除' if ok else '❌ 会话不存在'}", GREEN if ok else RED))
        return

    if args.doctor or args.fix:
        run_doctor()
        return

    if args.query or args.once:
        query = args.query or ""
        run_once(query, args.tools)
        return

    # 交互模式
    store = SessionStore()
    session = store.get_or_create_session(args.session)
    run_chat(session, args.tools)


if __name__ == "__main__":
    main()
