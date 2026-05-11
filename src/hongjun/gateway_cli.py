"""
鸿钧 · Gateway CLI
==================

直连鸿钧 Gateway HTTP API 的交互式命令行界面，支持 SSE 流式步骤展示。

用法：
  python -m hongjun.gateway_cli                    # 交互模式
  python -m hongjun.gateway_cli "1+1等于几"         # 单次模式
  python -m hongjun.gateway_cli --once "你是谁"    # 单次模式（同上）
  python -m hongjun.gateway_cli --no-steps "问题"   # 不显示执行步骤

依赖：仅标准库（urllib + json + readline + threading）。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
import threading
import queue as _queue
import datetime
import time
import io
from pathlib import Path

# ANSI 颜色
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"
MAGENTA = "\033[35m"
WHITE = "\033[37m"


def color(text: str, c: str) -> str:
    return f"{c}{text}{RESET}"


# ── 配置 ───────────────────────────────────────────────────────────────────

DEFAULT_GATEWAY = "http://localhost:20830"
TIMEOUT_SEC = 120


# ── SSE 事件类型 ─────────────────────────────────────────────────────────────

EVENT_TYPES = {
    "status": "ℹ️",
    "chunk": None,        # 直接合并到 accumulator
    "step": "🔧",
    "done": "✅",
    "error": "❌",
    "pending_approval": "⚠️",
}


# ── SSE 流式请求 ─────────────────────────────────────────────────────────────

def _stream_sse(
    gateway: str,
    message: str,
    session_id: str | None = None,
    verbose: bool = True,
) -> tuple[str, float]:
    """
    通过 SSE /stream 端点发送消息，返回 (final_response, elapsed_seconds)。

    实时解析 SSE 事件，按类型分发显示：
    - step: 显示执行步骤（意图解析/任务开始/任务完成）
    - status: 显示状态消息
    - chunk: 累积到响应文本
    - done: 返回最终文本
    - pending_approval: 显示审批请求

    使用后台线程读取 SSE流，主线程处理显示。
    """
    payload = {
        "message": message,
        "platform": "cli",
        "verbose": verbose,
    }
    if session_id:
        payload["session_id"] = session_id

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{gateway}/stream",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=None)  # 无超时，等 SSE 流自然结束
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"连接失败: {e.reason}。确认鸿钧 Gateway 是否在运行（默认 localhost:20830）")

    # SSE 行解析器
    def parse_sse_events(resp_io):
        """从 SSE 响应流中解析事件，逐个放入 event_queue。"""
        buffer = b""
        while True:
            try:
                chunk = resp_io.read(1024)
                if chunk == b"":
                    # 流结束
                    event_queue.put(("__DONE__", {}))
                    return
            except Exception:
                # 连接被提前关闭（如服务器重启/超时），安全退出
                event_queue.put(("__DONE__", {}))
                return

            buffer += chunk
            # 按 "data: " 或 "\n\n" 分割
            lines = buffer.split(b"\n")
            buffer = lines[-1]  # 保留不完整的行

            i = 0
            while i < len(lines) - 1:
                line = lines[i].decode("utf-8", errors="replace").strip()
                i += 1
                if line.startswith("data: "):
                    data_str = line[len("data: "):]
                    if data_str == "[DONE]":
                        event_queue.put(("__DONE__", {}))
                        return
                    try:
                        event = json.loads(data_str)
                        event_type = event.get("type", "unknown")
                        event_queue.put((event_type, event))
                    except json.JSONDecodeError:
                        pass
                elif line == "" and i > 1:
                    # 空行可能是 SSE 事件分隔符，继续处理
                    pass

        # 流结束
        event_queue.put(("__DONE__", {}))

    # 事件队列：元素为 (type, data_dict)
    event_queue: _queue.Queue = _queue.Queue()

    # 启动 SSE 读取线程
    reader_thread = threading.Thread(
        target=parse_sse_events,
        args=(resp,),
        daemon=True,
    )
    reader_thread.start()

    # 主线程处理事件
    accumulated = ""
    final_response = ""
    start_time = time.time()
    steps_shown = []
    pending_approval_shown = False
    step_count = 0

    def print_line(text, c=DIM, newline=True):
        if newline:
            print(color(f"  {text}", c))
        else:
            print(color(f"  {text}", c), end="", flush=True)

    while True:
        try:
            event_type, event = event_queue.get(timeout=TIMEOUT_SEC)
        except _queue.Empty:
            raise RuntimeError("SSE 流超时（无响应）")

        if event_type == "__DONE__":
            break

        elif event_type == "step":
            step = event.get("step", "")
            label = event.get("label", f"[{step}]")
            data = event.get("data", {})
            step_count += 1

            if step == "intent":
                intent = data.get("intent", "")
                subtasks = data.get("subtasks", [])
                print_line(f"", DIM)
                print_line(f"🎯 意图解析", CYAN)
                print_line(f"   意图: {intent}", WHITE)
                if subtasks:
                    for t in subtasks[:3]:
                        print_line(f"   → {t}", DIM)

            elif step == "task_start":
                skill = data.get("skill", "")
                task = data.get("task", "")
                print_line(f"", DIM)
                print_line(f"🚀 开始执行", YELLOW)
                print_line(f"   技能: {skill or task}", WHITE)

            elif step == "task_done":
                result = (data.get("result", "") or "")[:100]
                print_line(f"📋 任务完成", GREEN)
                if result:
                    print_line(f"   结果: {result}...", DIM)
                else:
                    print_line(f"   (无详细结果)", DIM)

            elif step == "final":
                response_preview = (data.get("response", "") or "")[:150]
                print_line(f"✅ 最终回复", GREEN)

        elif event_type == "status":
            content = event.get("content", "")
            if "分析意图" in content or "执行" in content:
                print_line(f"  {content}", YELLOW)
            else:
                print_line(f"  {content}", DIM)

        elif event_type == "chunk":
            chunk = event.get("content", "")
            accumulated += chunk
            print_line(chunk, CYAN, newline=False)

        elif event_type == "done":
            final_response = event.get("content", "") or accumulated
            print_line(f"", DIM)

        elif event_type == "error":
            err_msg = event.get("content", "未知错误")
            print_line(f"", DIM)
            print_line(f"❌ 错误: {err_msg}", RED)

        elif event_type == "pending_approval":
            if not pending_approval_shown:
                reason = event.get("reason", "")
                severity = event.get("severity", 0)
                severity_label = {5: "中", 8: "高", 10: "极高"}.get(severity, str(severity))
                print_line(f"", DIM)
                print_line(f"⚠️  危险操作待审批（等级: {severity_label}）", YELLOW)
                print_line(f"   原因: {reason}", WHITE)
                print_line(f"   命令: {message[:80]}", DIM)
                pending_approval_shown = True

    elapsed = time.time() - start_time
    reader_thread.join(timeout=2)

    return final_response or accumulated, elapsed


def _get_status(gateway: str) -> dict:
    """查询 gateway 状态。"""
    req = urllib.request.Request(f"{gateway}/status", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


# ── 会话历史 ──────────────────────────────────────────────────────────────────

SESSIONS_DIR = Path.home() / ".hongjun" / "cli_sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _load_history(session_id: str) -> list[dict]:
    f = SESSIONS_DIR / f"{session_id}.json"
    if f.exists():
        try:
            with open(f) as fp:
                return json.load(fp)
        except Exception:
            return []
    return []


def _save_history(session_id: str, history: list[dict]) -> None:
    f = SESSIONS_DIR / f"{session_id}.json"
    with open(f, "w") as fp:
        json.dump(history, fp, ensure_ascii=False)


# ── 渲染 ──────────────────────────────────────────────────────────────────────

def _render_response_final(final_response: str, latency: float, score: float = 0) -> None:
    """在单次/简略模式下渲染最终回复。"""
    print()
    for line in final_response.split("\n"):
        print(color(f"  {line}", CYAN))
    print()
    meta_parts = []
    if latency:
        meta_parts.append(color(f"{latency:.1f}s", DIM))
    if score:
        score_color = GREEN if score >= 0.7 else YELLOW if score >= 0.4 else RED
        meta_parts.append(color(f"得分 {score:.2f}", score_color))
    if meta_parts:
        print(color(f"  {'  |  '.join(meta_parts)}", DIM))


def _render_error(msg: str) -> None:
    print(color(f"\n  ❌ {msg}", RED), file=sys.stderr)


# ── 交互模式 ─────────────────────────────────────────────────────────────────

import readline  # noqa: E402


def _input_with_history(prompt: str) -> str:
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError):
        return ""


def _print_banner(gateway: str) -> None:
    status = _get_status(gateway)

    # ── 葫芦 ASCII Art（8行，左侧展示）──────────────────────────────────────
    GOURD_LINES = [
        "         ╭━━━━━╮",
        "       ╱         ╲",
        "      │  ◯     ◯  │",
        "      │    ╭━━╯   │",
        "      │   ╱       │",
        "       ╲ ╱    ╱╱╱╱",
        "        ╳    ╱╱╱╱",
        "       ╱ ╲  ╱╱╱╱",
    ]

    if "error" not in status:
        ver = status.get("version", "?")
        uptime = status.get("uptime", "?")
        HEADER = f"""{BOLD}{CYAN}
  ╔══════════════════════════════════════════════════════╗
  ║       鸿钧 Gateway CLI  (v{ver})                       ║
  ║       Gateway: {gateway}                  ║
  ║       版本: {ver}   在线: {uptime}                      ║
  ╚══════════════════════════════════════════════════════╝{RESET}"""
    else:
        HEADER = f"""{BOLD}{CYAN}
  ╔══════════════════════════════════════════════════════╗
  ║       鸿钧 Gateway CLI  (v?)                         ║
  ║       Gateway: {gateway}                              ║
  ║       ⚠️  Gateway 连接失败                               ║
  ╚══════════════════════════════════════════════════════╝{RESET}"""

    # ── 指令集（右侧，8条）────────────────────────────────────────────────
    instr_items = [
        ("exit / quit / q",  "退出交互模式"),
        ("/new",             "开启新会话"),
        ("!command",          "执行本地 shell 命令"),
        ("--no-steps",        "关闭执行步骤展示"),
        ("--gateway <port>",  "指定 Gateway 端口"),
        ("/skills",           "查看可用 Skills 列表"),
        ("/doctor",           "运行鸿钧自检"),
        ("/help",             "显示此帮助面板"),
    ]

    # 左右拼版：左侧葫芦宽12字符，右侧指令集面板
    logo_col_w = 16   # 葫芦区总宽度
    logo_padding = 2  # 葫芦与右边框间距

    print(HEADER)
    print()

    # 面板顶部
    print(f"  {CYAN}╔{'═' * (logo_col_w + logo_padding)}╗╔{'═' * 44}╗{RESET}")
    # 标题行（跨越左右）
    print(f"  {CYAN}║{RESET}{' ' * (logo_col_w + logo_padding)}{CYAN}║{RESET}  {BOLD}{CYAN}🧧  鸿 钧 · 指 令 集{CYAN}{RESET}                      {CYAN}║{RESET}")
    print(f"  {CYAN}╠{'═' * (logo_col_w + logo_padding)}╣╠{'═' * 44}╣{RESET}")

    # 逐行渲染葫芦 + 指令（指令比葫芦多时葫芦区留空）
    for i, (cmd, desc) in enumerate(instr_items):
        if i < len(GOURD_LINES):
            gourd = GOURD_LINES[i]
            # 葫芦区：左 border + gourd + padding + 右 border
            logo_cell = f"{CYAN}║{RESET}{CYAN}{gourd}{' ' * (logo_col_w - len(gourd) - logo_padding)}{RESET}{CYAN} ║{RESET}"
        else:
            # 葫芦行结束后，左侧填空格
            logo_cell = f"{CYAN}║{RESET}{' ' * logo_col_w}{CYAN} ║{RESET}"

        instr_cell = f"  {WHITE}{BOLD}{cmd:<22}{RESET}{GREEN}—{RESET}  {WHITE}{desc:<18}{RESET}  {CYAN}║{RESET}"
        print(f"{logo_cell}{instr_cell}")

    # 面板底部
    print(f"  {CYAN}╚{'━' * (logo_col_w + logo_padding)}╝╚{'━' * 44}╝{RESET}")


def run_chat(
    gateway: str,
    session_id: str | None = None,
    show_steps: bool = True,
) -> None:
    """交互式聊天（默认 SSE 流式 + 步骤展示）。"""
    if session_id is None:
        session_id = f"cli-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"

    history = _load_history(session_id)
    _print_banner(gateway)

    if history:
        print(color(f"\n  加载历史会话: {session_id}  ({len(history)} 条消息)\n", DIM))

    print(color(f"  会话ID: {session_id}", DIM))
    if show_steps:
        print(color(f"  步骤展示: 开启（--no-steps 关闭）", DIM))
    print()

    prompt_count = len([m for m in history if m["role"] == "user"])

    while True:
        try:
            user_input = _input_with_history(
                color(f"[{prompt_count + 1}] {BOLD}你{RESET}> ", GREEN)
            )
        except (KeyboardInterrupt, EOFError):
            print("\n")
            print(color("👋 再见！", CYAN))
            break

        if not user_input or user_input.strip().lower() in ("exit", "quit", "q"):
            print(color("👋 再见！", CYAN))
            break

        if not user_input.strip():
            continue

        user_input = user_input.strip()
        prompt_count += 1

        # 保存用户消息
        history.append({"role": "user", "content": user_input})
        _save_history(session_id, history)

        # 追加到 readline 历史
        readline.add_history(user_input)

        # SSE 流式请求
        try:
            final_response, elapsed = _stream_sse(
                gateway, user_input, session_id, verbose=show_steps
            )
            _render_response_final(final_response, elapsed)
        except RuntimeError as e:
            _render_error(str(e))
            print(color("  ⚠️  仍可继续输入，Gateway 恢复后会正常", DIM))
            continue

        # 保存助手回复
        history.append({"role": "assistant", "content": final_response})
        _save_history(session_id, history)


# ── 单次模式 ─────────────────────────────────────────────────────────────────

def run_once(
    gateway: str,
    query: str,
    show_steps: bool = True,
) -> None:
    """单次查询（默认 SSE 流式 + 步骤展示）。"""
    try:
        final_response, elapsed = _stream_sse(
            gateway, query, None, verbose=show_steps
        )
        _render_response_final(final_response, elapsed)
    except RuntimeError as e:
        _render_error(str(e))
        sys.exit(1)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="hongjun",
        description="鸿钧 Gateway CLI — 直连 Gateway SSE 流式 API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m hongjun.gateway_cli                        # 交互模式
  python -m hongjun.gateway_cli "你好"                 # 单次查询
  python -m hongjun.gateway_cli --once "你是谁"       # 单次查询（同上）
  python -m hongjun.gateway_cli --no-steps "你好"     # 不显示执行步骤
  python -m hongjun.gateway_cli --gateway 20830 "你好" # 指定端口
        """,
    )
    parser.add_argument(
        "query", nargs="?", help="单次查询（指定后以单次模式运行）"
    )
    parser.add_argument(
        "--once", action="store_true", help="单次查询模式"
    )
    parser.add_argument(
        "--gateway", "-g", default=DEFAULT_GATEWAY,
        help=f"Gateway 地址（默认: {DEFAULT_GATEWAY}）",
    )
    parser.add_argument(
        "--session", "-s", help="指定会话 ID（交互模式）",
    )
    parser.add_argument(
        "--no-steps", dest="show_steps", action="store_false", default=True,
        help="不显示执行步骤（仅显示最终回复）",
    )

    args = parser.parse_args()

    if args.query or args.once:
        if not args.query:
            print("错误: 请输入查询内容", file=sys.stderr)
            sys.exit(1)
        run_once(args.gateway, args.query, show_steps=args.show_steps)
    else:
        run_chat(args.gateway, args.session, show_steps=args.show_steps)


if __name__ == "__main__":
    main()
