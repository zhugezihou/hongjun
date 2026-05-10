#!/usr/bin/env python3
"""
鸿钧 · 六部 A2A Server 管理脚本
================================

启动/停止/管理六部 A2A Server。

用法：
  # 启动全部六部
  python3 scripts/start_six_ministries.py --start

  # 停止全部六部
  python3 scripts/start_six_ministries.py --stop

  # 查看状态
  python3 scripts/start_six_ministries.py --status

  # 重启指定部门
  python3 scripts/start_six_ministries.py --restart 礼部

  # 启动单个部门
  python3 scripts/start_six_ministries.py --start 工部
"""

import sys
import os
import time
import signal
import subprocess
import httpx
from typing import Dict, List, Optional

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# === 六部配置 ===
MINISTRIES = {
    "吏部": {
        "port": 20020,
        "name": "协调中心",
        "description": "任务编排与调度",
    },
    "工部": {
        "port": 20021,
        "name": "执行引擎",
        "description": "代码生成与命令执行",
    },
    "户部": {
        "port": 20022,
        "name": "记忆系统",
        "description": "MemPalace + SQLite 记忆",
    },
    "礼部": {
        "port": 20023,
        "name": "工具层",
        "description": "浏览器自动化与搜索",
    },
    "兵部": {
        "port": 20024,
        "name": "安全护栏",
        "description": "输入输出过滤",
    },
    "刑部": {
        "port": 20025,
        "name": "质量评测",
        "description": "DeepEval 质量评估",
    },
}

PID_DIR = "/home/asus/hongjun/.pids"
PID_FILE = os.path.join(PID_DIR, "six_ministries.pid")


def ensure_pid_dir():
    os.makedirs(PID_DIR, exist_ok=True)


def load_pids() -> Dict[str, int]:
    """加载已有 PID"""
    ensure_pid_dir()
    if not os.path.exists(PID_FILE):
        return {}
    try:
        with open(PID_FILE) as f:
            return {k: int(v) for k, v in (line.strip().split(":") for line in f if ":" in line)}
    except Exception:
        return {}


def save_pids(pids: Dict[str, int]):
    """保存 PID"""
    ensure_pid_dir()
    with open(PID_FILE, "w") as f:
        for name, pid in pids.items():
            f.write(f"{name}:{pid}\n")


def check_alive(port: int) -> bool:
    """检查端口是否存活"""
    try:
        resp = httpx.get(f"http://localhost:{port}/", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def get_a2a_handler(ministry: str):
    """获取各部门的 A2A 处理器"""
    if ministry == "工部":
        from hongjun.工部_executor import HongjunExecutor
        executor = HongjunExecutor()

        def handler(task, server):
            result = executor.execute(task.task)
            return result
        return handler

    elif ministry == "户部":
        from hongjun.户部_memory import HongjunMemory
        mem = HongjunMemory(user_id="a2a")

        def handler(task, server):
            # 从 task.context 读取参数
            ctx = task.context or {}
            action = ctx.get("action", "recall")
            if action == "remember":
                return mem.remember(ctx.get("content", ""), ctx.get("importance", 0.5))
            elif action == "recall":
                return mem.recall(ctx.get("query", ""))
            else:
                return mem.status()
        return handler

    elif ministry == "礼部":
        from hongjun.礼部_tools import TOOL_REGISTRY

        def handler(task, server):
            ctx = task.context or {}
            tool = ctx.get("tool", "search")
            return TOOL_REGISTRY.call(tool, **ctx.get("params", {})).content
        return handler

    elif ministry == "兵部":
        from hongjun.兵部_guardrails import HongjunSecurity
        security = HongjunSecurity()

        def handler(task, server):
            ctx = task.context or {}
            action = ctx.get("action", "check_input")
            if action == "check_input":
                passed, error = security.check_input(task.task)
                return f"{'PASS' if passed else 'BLOCK'}: {error or 'OK'}"
            else:
                return security.check_output(task.task)
        return handler

    elif ministry == "刑部":
        from hongjun.刑部_evaluation import HongjunEvaluator
        evaluator = HongjunEvaluator()

        def handler(task, server):
            ctx = task.context or {}
            return evaluator.evaluate(
                ctx.get("task", ""),
                ctx.get("result", ""),
                ctx.get("execution_time_ms", 0),
            )
        return handler

    elif ministry == "吏部":
        from hongjun.吏部_coordinator import process_request

        def handler(task, server):
            return process_request(task.task)
        return handler

    else:
        # 默认处理器
        def default_handler(task, server):
            return f"[{ministry}] 已收到任务: {task.task}"
        return default_handler


def start_ministry(name: str, port: int) -> Optional[int]:
    """启动单个部门的 A2A Server"""
    if check_alive(port):
        print(f"  ✅ {name} 已在运行 (端口 {port})")
        return None

    try:
        from hongjun.protocol.a2a_server import start_a2a_server
        handler = get_a2a_handler(name)
        server = start_a2a_server(
            agent_id=name,
            port=port,
            task_handler=handler,
            background=True,
        )
        # 获取实际 pid
        time.sleep(0.5)
        if check_alive(port):
            print(f"  ✅ {name} 已启动 (端口 {port})")
            return os.getpid()  # 简化处理，实际应在后台进程
        else:
            print(f"  ❌ {name} 启动失败")
            return None
    except Exception as e:
        print(f"  ❌ {name} 启动异常: {e}")
        return None


def stop_ministry(name: str, port: int) -> bool:
    """停止单个部门的 A2A Server"""
    if not check_alive(port):
        print(f"  ℹ️  {name} 未运行")
        return True

    try:
        # 发送 shutdown 请求
        resp = httpx.get(f"http://localhost:{port}/shutdown", timeout=3)
        print(f"  ✅ {name} 已停止")
        return True
    except Exception:
        print(f"  ⚠️  {name} 无法优雅停止，尝试强制结束")
        return False


def print_status():
    """打印六部状态"""
    print()
    print("鸿钧 · 六部 A2A Server 状态")
    print("=" * 50)

    all_alive = True
    for name, config in MINISTRIES.items():
        port = config["port"]
        alive = check_alive(port)
        status = "✅ 运行中" if alive else "❌ 已停止"
        if not alive:
            all_alive = False
        print(f"  {name:4s} ({config['name']:6s}) 端口 {port:5d}  {status}")

    print()
    if all_alive:
        print("🎉 六部全部在线！")
    else:
        print("⚠️  部分部门未运行，使用 --start 启动")

    return all_alive


def start_all() -> List[str]:
    """启动全部六部"""
    print("🚀 启动六部 A2A Server...")
    started = []
    for name, config in MINISTRIES.items():
        pid = start_ministry(name, config["port"])
        if pid:
            started.append(name)
    return started


def stop_all():
    """停止全部六部"""
    print("🛑 停止六部 A2A Server...")
    for name, config in reversed(list(MINISTRIES.items())):
        stop_ministry(name, config["port"])


# === 命令行入口 ===
def main():
    import argparse

    parser = argparse.ArgumentParser(description="六部 A2A Server 管理")
    parser.add_argument("--start", action="store_true", help="启动全部")
    parser.add_argument("--stop", action="store_true", help="停止全部")
    parser.add_argument("--status", action="store_true", help="查看状态")
    parser.add_argument("--restart", metavar="部门", help="重启指定部门（如 工部）")
    parser.add_argument("--start-one", metavar="部门", help="启动单个部门")

    args = parser.parse_args()

    if args.status:
        print_status()
        return

    if args.start:
        started = start_all()
        print_status()
        return

    if args.stop:
        stop_all()
        print()
        print_status()
        return

    if args.restart:
        name = args.restart
        if name not in MINISTRIES:
            print(f"❌ 未知部门: {name}")
            print(f"   可选: {', '.join(MINISTRIES.keys())}")
            return
        port = MINISTRIES[name]["port"]
        stop_ministry(name, port)
        time.sleep(0.5)
        start_ministry(name, port)
        print_status()
        return

    if args.start_one:
        name = args.start_one
        if name not in MINISTRIES:
            print(f"❌ 未知部门: {name}")
            return
        port = MINISTRIES[name]["port"]
        start_ministry(name, port)
        return

    # 默认：打印状态
    print_status()


if __name__ == "__main__":
    main()
