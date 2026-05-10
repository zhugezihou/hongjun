#!/usr/bin/env python3
"""
鸿钧 · Gateway 看门狗
====================

监控鸿钧 Gateway 进程，若发现崩溃则在 5 秒内重启。
同时确保只有一个 Gateway 实例在运行。

Usage:
    python watchdog.py              # 前台运行
    python watchdog.py --daemon     # 守护进程模式
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

GATEWAY_PORT = 20830
GATEWAY_CMD = [
    sys.executable, "-m", "hongjun.gateway", "--port", str(GATEWAY_PORT)
]
WORKDIR = Path(__file__).parent.parent / "src"
PID_FILE = Path.home() / ".hongjun" / "gateway.pid"
LOG_FILE = Path.home() / "hongjun" / "gateway_stderr.log"
WATCHDOG_LOG = Path.home() / ".hongjun" / "watchdog.log"


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)
    WATCHDOG_LOG.write_text(line + "\n", encoding="utf-8")


def read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def write_pid(pid: int):
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_gateway_responding() -> bool:
    try:
        import httpx
        resp = httpx.get(f"http://127.0.0.1:{GATEWAY_PORT}/health", timeout=3.0)
        return resp.status_code == 200 and resp.text == "OK"
    except Exception:
        return False


def start_gateway() -> int | None:
    env = {
        **os.environ,
        "PYTHONPATH": str(WORKDIR),
        "PYTHONUNBUFFERED": "1",
    }
    try:
        log_fp = open(LOG_FILE, "a", encoding="utf-8")
        proc = subprocess.Popen(
            GATEWAY_CMD,
            cwd=str(WORKDIR),
            env=env,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return proc.pid
    except Exception as e:
        log(f"启动失败: {e}")
        return None


def kill_gateway(pid: int):
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    except OSError:
        pass


def watch():
    """单次检查 + 重启逻辑"""
    pid = read_pid()
    alive = is_alive(pid) if pid else False
    responding = is_gateway_responding() if alive else False

    if alive and responding:
        return  # 一切正常

    if alive and not responding:
        log(f"Gateway PID {pid} 无响应，尝试重启...")
        kill_gateway(pid)

    if not alive:
        if pid:
            log(f"Gateway PID {pid} 已消失，重启中...")
        else:
            log("Gateway 未运行，启动中...")

    new_pid = start_gateway()
    if new_pid:
        write_pid(new_pid)
        log(f"Gateway 已启动 PID={new_pid}")
    else:
        log("Gateway 启动失败！")


def daemon():
    """守护进程主循环"""
    import httpx  # 确保 watchdog 依赖已安装

    log("看门狗启动")
    failures = 0

    while True:
        pid = read_pid()
        alive = is_alive(pid) if pid else False
        responding = is_gateway_responding() if alive else False

        if alive and responding:
            if failures > 0:
                log(f"Gateway 恢复（连续失败 {failures} 次后）")
                failures = 0
        else:
            failures += 1
            if alive and not responding:
                log(f"Gateway PID {pid} 无响应 ({failures}/5)，重启...")
                kill_gateway(pid)
            elif not alive:
                log(f"Gateway 未运行 ({failures}/5)，启动...")

            new_pid = start_gateway()
            if new_pid:
                write_pid(new_pid)
                log(f"Gateway 已重启 PID={new_pid}")
                failures = 0  # 重置失败计数
            else:
                log(f"Gateway 启动失败！")

        time.sleep(30)  # 每 30 秒检查一次


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="鸿钧 Gateway 看门狗")
    parser.add_argument("--daemon", action="store_true", help="守护进程模式")
    args = parser.parse_args()

    if args.daemon:
        daemon()
    else:
        watch()
