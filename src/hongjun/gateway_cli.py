"""
鸿钧 · Gateway CLI
==================

直连鸿钧 Gateway HTTP API 的交互式命令行界面。

用法：
  python -m hongjun.gateway_cli                    # 交互模式
  python -m hongjun.gateway_cli "1+1等于几"         # 单次模式
  python -m hongjun.gateway_cli --once "你是谁"    # 单次模式（同上）

依赖：仅标准库（urllib + json + readline）。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
import urllib.parse
import datetime
import time
import uuid
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


def color(text: str, c: str) -> str:
    return f"{c}{text}{RESET}"


# ── 配置 ───────────────────────────────────────────────────────────────────

DEFAULT_GATEWAY = "http://localhost:20830"
TIMEOUT_SEC = 120


# ── Gateway 通信 ─────────────────────────────────────────────────────────────

def _post_chat(gateway: str, message: str, session_id: str | None = None) -> dict:
    """发送消息到 gateway /chat，返回 JSON 响应字典。"""
    payload = {"message": message}
    if session_id:
        payload["session_id"] = session_id

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{gateway}/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"连接失败: {e.reason}。确认鸿钧 Gateway 是否在运行（默认 localhost:20830）")


def _get_status(gateway: str) -> dict:
    """查询 gateway 状态。"""
    req = urllib.request.Request(f"{gateway}/status", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


# ── 会话历史 ────────────────────────────────────────────────────────────────

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


# ── 渲染 ────────────────────────────────────────────────────────────────────

def _render_response(resp: dict) -> None:
    """渲染 /chat 响应到终端。"""
    response_text = resp.get("response", "(无回复)")
    latency = resp.get("latency_s", 0)
    score = resp.get("eval_score", 0)
    msg_count = resp.get("message_count", 0)

    print()
    # 助手回复
    for line in response_text.split("\n"):
        print(color(f"  {line}", CYAN))
    print()

    # 元信息
    meta_parts = []
    if latency:
        meta_parts.append(color(f"{latency:.1f}s", DIM))
    if score:
        score_color = GREEN if score >= 0.7 else YELLOW if score >= 0.4 else RED
        meta_parts.append(color(f"得分 {score:.2f}", score_color))
    if msg_count:
        meta_parts.append(color(f"轮次 {msg_count}", DIM))

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
    if "error" not in status:
        ver = status.get("version", "?")
        uptime = status.get("uptime", "?")
        banner = f"""{BOLD}{CYAN}
  ╔════════════════════════════════════════╗
  ║     鸿钧 Gateway CLI  (v1.0.0)         ║
  ║     Gateway: {gateway}      ║
  ║     版本: {ver}   在线: {uptime}           ║
  ║     输入 exit/quit 退出                 ║
  ╚════════════════════════════════════════╝{RESET}"""
    else:
        banner = f"""{BOLD}{CYAN}
  ╔════════════════════════════════════════╗
  ║     鸿钧 Gateway CLI  (v1.0.0)         ║
  ║     Gateway: {gateway}              ║
  ║     ⚠️  Gateway 连接失败              ║
  ╚════════════════════════════════════════╝{RESET}"""
    print(banner)


def run_chat(gateway: str, session_id: str | None = None) -> None:
    """交互式聊天。"""
    if session_id is None:
        session_id = f"cli-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"

    history = _load_history(session_id)
    _print_banner(gateway)

    if history:
        print(color(f"\n  加载历史会话: {session_id}  ({len(history)} 条消息)\n", DIM))

    print(color(f"  会话ID: {session_id}", DIM))
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

        # 发请求
        print(color("  ⏳ 等待回复...", DIM), flush=True)
        t0 = time.time()

        try:
            resp = _post_chat(gateway, user_input, session_id)
            elapsed = time.time() - t0
            _render_response(resp)
        except RuntimeError as e:
            _render_error(str(e))
            print(color("  ⚠️  仍可继续输入，Gateway 恢复后会正常", DIM))
            continue

        # 保存助手回复
        history.append({"role": "assistant", "content": resp.get("response", "")})
        _save_history(session_id, history)


# ── 单次模式 ─────────────────────────────────────────────────────────────────

def run_once(gateway: str, query: str) -> None:
    """单次查询。"""
    print(color(f"  ⏳ 等待回复...", DIM), flush=True)
    t0 = time.time()
    try:
        resp = _post_chat(gateway, query)
        elapsed = time.time() - t0
        _render_response(resp)
    except RuntimeError as e:
        _render_error(str(e))
        sys.exit(1)


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="hongjun",
        description="鸿钧 Gateway CLI — 直连 Gateway HTTP API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m hongjun.gateway_cli                        # 交互模式
  python -m hongjun.gateway_cli "你好"                 # 单次查询
  python -m hongjun.gateway_cli --once "你是谁"        # 单次查询（同上）
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

    args = parser.parse_args()

    if args.query or args.once:
        if not args.query:
            print("错误: 请输入查询内容", file=sys.stderr)
            sys.exit(1)
        run_once(args.gateway, args.query)
    else:
        run_chat(args.gateway, args.session)


if __name__ == "__main__":
    main()
