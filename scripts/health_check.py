#!/usr/bin/env python3
"""
鸿钧 · 健康检查 & 刑部监控
===========================

功能：
  1. 六部 A2A Server 端口健康检查
  2. 鸿钧主进程存活检查
  3. 关键指标（响应时间/评分/内存）
  4. 异常告警

用法：
  # 单次检查
  python3 scripts/health_check.py

  # 持续监控（每 30 秒一次）
  python3 scripts/health_check.py --watch

  # Docker 环境检查
  python3 scripts/health_check.py --docker
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.request import urlopen
from urllib.error import URLError

# === 配置 ===

A2A_PORTS = {
    "工部": 20002,
    "户部": 20003,
    "礼部": 20004,
    "兵部": 20005,
    "刑部": 20006,
    "吏部": 20007,
}
STATUS_API = "http://localhost:20099/status"
MAIN_PORT = 8000

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
NC = "\033[0m"


@dataclass
class HealthResult:
    service: str
    port: int
    status: str  # healthy / unhealthy / unknown
    latency_ms: float = 0
    message: str = ""


def check_http(url: str, timeout: float = 3.0) -> tuple[bool, float, str]:
    """检查 HTTP 端点是否可达"""
    try:
        start = time.time()
        req = urlopen(url, timeout=timeout)
        latency = (time.time() - start) * 1000
        code = req.getcode()
        if 200 <= code < 400:
            return True, latency, f"HTTP {code}"
        return False, latency, f"HTTP {code}"
    except URLError as e:
        return False, 0, str(e.reason)
    except Exception as e:
        return False, 0, str(e)


def check_a2a_port(name: str, port: int) -> HealthResult:
    """检查单个 A2A Server 端口

    A2A Server 只接受 POST 请求，GET 返回 405 Method Not Allowed。
    因此 "Method Not Allowed" 实际上说明服务正常运行。
    """
    url = f"http://localhost:{port}/"
    ok, latency, msg = check_http(url, timeout=2.0)
    # Method Not Allowed = 服务可达，只是用了错误的方法
    if msg == "Method Not Allowed":
        ok = True
        msg = "服务正常（仅接受POST）"
    return HealthResult(
        service=name,
        port=port,
        status="healthy" if ok else "unhealthy",
        latency_ms=latency,
        message=msg,
    )


def check_six_ministries() -> list[HealthResult]:
    """检查六部 A2A Server 全部端口"""
    results = []
    for name, port in A2A_PORTS.items():
        results.append(check_a2a_port(name, port))
    return results


def check_status_api() -> HealthResult:
    """检查状态聚合 API"""
    ok, latency, msg = check_http(f"http://localhost:20099/status", timeout=2.0)
    return HealthResult(
        service="状态API",
        port=20099,
        status="healthy" if ok else "unhealthy",
        latency_ms=latency,
        message=msg,
    )


def check_hongjun_process() -> HealthResult:
    """检查鸿钧主进程是否存活"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "hongjun.py"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split("\n")
            return HealthResult(
                service="鸿钧进程",
                port=0,
                status="healthy",
                message=f"PIDs: {', '.join(pids)}",
            )
        else:
            return HealthResult(
                service="鸿钧进程",
                port=0,
                status="unhealthy",
                message="进程未运行",
            )
    except Exception as e:
        return HealthResult(
            service="鸿钧进程",
            port=0,
            status="unknown",
            message=str(e),
        )


def check_memory() -> dict:
    """获取鸿钧进程内存使用"""
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        for line in result.stdout.splitlines():
            if "hongjun.py" in line:
                parts = line.split()
                if len(parts) >= 6:
                    return {
                        "pid": parts[1],
                        "cpu": parts[2],
                        "mem": parts[3],
                        "status": "running",
                    }
    except Exception:
        pass
    return {"status": "not_found"}


def run_e2e_test() -> tuple[bool, float, str]:
    """运行快速端到端测试"""
    try:
        start = time.time()
        result = subprocess.run(
            ["python3", "/home/asus/hongjun/src/hongjun.py", "你好"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd="/home/asus/hongjun",
            env={"PATH": "/home/asus/.hermes/hermes-agent/venv/bin:/usr/bin:/bin"},
        )
        elapsed = (time.time() - start) * 1000
        ok = result.returncode == 0
        return ok, elapsed, "成功" if ok else f"失败: {result.stderr[:100]}"
    except subprocess.TimeoutExpired:
        return False, 15000, "超时（>15s）"
    except Exception as e:
        return False, 0, str(e)


def print_result(r: HealthResult, verbose: bool = False):
    """打印单个检查结果"""
    icon = "✅" if r.status == "healthy" else ("⚠️" if r.status == "unknown" else "❌")
    if r.port > 0:
        print(f"  {icon} {r.service}({r.port}): {r.status} {f'({r.latency_ms:.0f}ms)' if r.latency_ms else ''} {r.message}")
    else:
        print(f"  {icon} {r.service}: {r.status} {r.message}")


def print_report(all_results: list[HealthResult], mem_info: dict, e2e_ok: bool, e2e_ms: float):
    """打印完整健康报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"鸿钧 · 刑部健康检查  {now}")
    print(f"{'='*50}")

    healthy = sum(1 for r in all_results if r.status == "healthy")
    total = len(all_results)
    overall = "✅ 健康" if healthy == total else ("⚠️ 部分异常" if healthy > 0 else "❌ 严重异常")
    print(f"\n总体状态: {overall} ({healthy}/{total} 服务正常)")

    print(f"\n【六部 A2A Server】")
    for r in all_results:
        print_result(r)

    print(f"\n【资源使用】")
    if mem_info.get("status") == "running":
        print(f"  ✅ 进程运行中 | PID: {mem_info['pid']} | CPU: {mem_info['cpu']}% | 内存: {mem_info['mem']}%")
    else:
        print(f"  ⚠️ {mem_info.get('status', '未知')}")

    print(f"\n【端到端测试】")
    if e2e_ok:
        print(f"  ✅ 响应正常 ({e2e_ms:.0f}ms)")
    else:
        print(f"  ❌ 测试失败: {e2e_ms}")


def main():
    parser = argparse.ArgumentParser(description="鸿钧健康检查")
    parser.add_argument("--watch", "-w", action="store_true", help="持续监控模式（每30秒）")
    parser.add_argument("--docker", "-d", action="store_true", help="Docker 环境检查")
    parser.add_argument("--e2e", "-e", action="store_true", help="运行端到端测试")
    parser.add_argument("--interval", "-i", type=int, default=30, help="监控间隔（秒）")
    args = parser.parse_args()

    if args.docker:
        print("Docker 环境检查...")
        A2A_PORTS_DOCKER = {k: v for k, v in A2A_PORTS.items()}
        for name, port in A2A_PORTS_DOCKER.items():
            try:
                ok, lat, _ = check_http(f"http://host.docker.internal:{port}/", timeout=2.0)
                print(f"  {'✅' if ok else '❌'} {name}:{port} ({lat:.0f}ms)" if ok else f"  ❌ {name}:{port}")
            except Exception as e:
                print(f"  ❌ {name}:{port} - {e}")
        return

    if args.watch:
        print(f"持续监控模式（间隔 {args.interval}s，Ctrl+C 退出）")
        while True:
            try:
                results = check_six_ministries()
                results.append(check_status_api())
                results.append(check_hongjun_process())
                mem = check_memory()
                e2e_ok, e2e_ms, _ = run_e2e_test() if args.e2e else (True, 0, "")
                print_report(results, mem, e2e_ok, e2e_ms)
                time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\n监控已停止")
                break
    else:
        # 单次检查
        results = check_six_ministries()
        results.append(check_status_api())
        results.append(check_hongjun_process())
        mem = check_memory()
        e2e_ok, e2e_ms, _ = run_e2e_test() if args.e2e else (True, 0, "")
        print_report(results, mem, e2e_ok, e2e_ms)


if __name__ == "__main__":
    main()
