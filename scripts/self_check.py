#!/usr/bin/env python3
"""
Hongjun 定时自检脚本
====================

每30分钟自动运行一次，检查：
1. Gateway 健康状态
2. 代码库诊断（语法/导入错误）
3. 近期失败任务模式
4. 自动修复能力

输出：
- 正常 → 静默
- 发现问题 → 推送飞书通知到朝堂群
- 自动修复 → 推送修复记录

Usage:
    python self_check.py              # 检查并报告
    python self_check.py --fix        # 检查+自动修复
    python self_check.py --daemon     # 守护进程模式（每30分钟一次）
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# 确保可以导入 hongjun
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hongjun.self_repair import SelfRepairEngine
from hongjun.evolution_memory import EvolutionMemory
from hongjun.logging_config import get_logger

logger = get_logger("hongjun.self_check")


GATEWAY_PORT = 20830
GATEWAY_CMD = [sys.executable, "-m", "hongjun.gateway", "--port", str(GATEWAY_PORT)]
WORKDIR = Path(__file__).parent.parent / "src"
PID_FILE = Path.home() / ".hongjun" / "gateway.pid"
LOG_FILE = Path.home() / "hongjun" / "gateway_stderr.log"


def check_gateway() -> dict:
    """检查 Gateway 健康"""
    try:
        import httpx
        resp = httpx.get(f"http://127.0.0.1:{GATEWAY_PORT}/health", timeout=5.0)
        ok = resp.status_code == 200 and resp.text == "OK"
        return {"status": "OK" if ok else "UNHEALTHY", "code": resp.status_code, "body": resp.text}
    except Exception as e:
        return {"status": "DOWN", "error": str(e)}


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def restart_gateway() -> dict:
    """重启 Gateway 进程，返回结果"""
    pid = _read_pid()
    alive = _is_alive(pid) if pid else False
    responding = False
    if alive:
        gw_check = check_gateway()
        responding = gw_check["status"] == "OK"

    if alive and responding:
        return {"action": "none", "reason": "gateway_already_running", "pid": pid}

    # 需要重启
    if alive and not responding:
        logger.warning(f"Gateway PID {pid} 无响应，尝试重启...")
        try:
            os.kill(pid, 15)  # SIGTERM
            time.sleep(2)
            try:
                os.kill(pid, 9)  # SIGKILL
            except OSError:
                pass
        except OSError:
            pass

    # 启动新进程
    env = {**os.environ, "PYTHONPATH": str(WORKDIR), "PYTHONUNBUFFERED": "1"}
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        log_fp = open(LOG_FILE, "a", encoding="utf-8")
        proc = subprocess.Popen(
            GATEWAY_CMD,
            cwd=str(WORKDIR),
            env=env,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        new_pid = proc.pid
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(new_pid), encoding="utf-8")
        logger.info(f"Gateway 重启成功 PID={new_pid}")
        return {"action": "restarted", "pid": new_pid}
    except Exception as e:
        logger.error(f"Gateway 重启失败: {e}")
        return {"action": "failed", "error": str(e)}


def check_logs() -> list[str]:
    """检查最近日志中的错误"""
    log_path = Path.home() / "hongjun" / "gateway_stderr.log"
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").split("\n")
        errors = []
        for line in lines[-500:]:  # 只检查最近500行
            lower = line.lower()
            if any(k in lower for k in ["error", "exception", "traceback", "failed", "critical"]):
                if "LangChainPendingDeprecationWarning" in line:
                    continue
                errors.append(line.strip()[:200])
        return errors[-20:]  # 最多20条
    except Exception:
        return []


def run_check(fix: bool = False, restart: bool = True) -> dict:
    """执行完整自检

    Args:
        fix: 是否尝试修复代码问题
        restart: 是否在 Gateway DOWN 时自动重启
    """
    report = {
        "timestamp": time.time(),
        "gateway": check_gateway(),
        "errors_in_log": check_logs(),
        "diagnostics": None,
        "repairs_attempted": [],
        "notifications_sent": 0,
        "status": "OK",
        "gateway_restart": None,
    }

    # Gateway 进程级检查：DOWN 则尝试重启
    if report["gateway"]["status"] != "OK" and restart:
        logger.warning(f"Gateway 状态异常: {report['gateway']['status']}，尝试重启...")
        restart_result = restart_gateway()
        report["gateway_restart"] = restart_result
        # 等待一下再检查
        time.sleep(5)
        new_status = check_gateway()
        report["gateway"] = new_status
        if new_status["status"] == "OK":
            report["status"] = "GATEWAY_RESTORED"
        else:
            report["status"] = "GATEWAY_DOWN"

    # 运行代码诊断
    engine = SelfRepairEngine()
    diag = engine.run_diagnostics()
    report["diagnostics"] = diag.summary()

    if diag.issues:
        if report["status"] == "OK":
            report["status"] = "ISSUES_FOUND"
        for issue in diag.issues:
            if issue.severity == "critical":
                logger.error(f"Critical issue in {issue.module}: {issue.description}")
                if fix and issue.module:
                    results = engine.fix_module(issue.module, f"{issue.description} at line {issue.line}")
                    report["repairs_attempted"].extend(results)
                    if results and results[0].success:
                        report["status"] = "AUTO_REPAIRED"
            elif issue.severity == "error":
                logger.warning(f"Error in {issue.module}: {issue.description}")

    return report


def format_report(report: dict) -> str:
    """格式化报告为飞书友好的文本"""
    lines = ["🔍 **鸿钧自检报告**"]

    # Gateway
    gw = report["gateway"]
    gw_icon = "✅" if gw["status"] == "OK" else "❌"
    lines.append(f"{gw_icon} **Gateway**: {gw['status']}")

    # Gateway 重启记录
    gr = report.get("gateway_restart")
    if gr:
        if gr.get("action") == "restarted":
            lines.append(f"  🔄 已重启 PID={gr.get('pid')}")
        elif gr.get("action") == "failed":
            lines.append(f"  ❌ 重启失败: {gr.get('error')}")

    # 日志错误
    errors = report.get("errors_in_log", [])
    if errors:
        lines.append(f"\n⚠️ **日志错误** ({len(errors)} 条)：")
        seen = set()
        for e in errors[:5]:
            short = e[:100]
            if short not in seen:
                seen.add(short)
                lines.append(f"  • `{short}`")

    # 诊断
    diag = report.get("diagnostics")
    if diag:
        issues = diag.get("issues", [])
        if issues:
            lines.append(f"\n🔧 **代码诊断**: 发现 {len(issues)} 个问题")
            for iss in issues[:5]:
                lines.append(f"  • [{iss['severity'].upper()}] {iss['module']}: {iss['description'][:80]}")
        else:
            lines.append(f"\n✅ **代码诊断**: 所有模块正常 ({diag['modules_ok']} 个模块)")

    # 修复
    repairs = report.get("repairs_attempted", [])
    if repairs:
        lines.append(f"\n🔧 **自动修复**: {len(repairs)} 次")
        for r in repairs:
            status = "✅" if r.success else "❌"
            lines.append(f"  {status} {r.module}: {r.description[:80]}")

    status = report.get("status", "UNKNOWN")
    if status == "OK":
        lines.append(f"\n✅ 状态：**一切正常**")
    elif status == "GATEWAY_RESTORED":
        lines.append(f"\n🔄 状态：**Gateway 已自动恢复**")
    elif status == "GATEWAY_DOWN":
        lines.append(f"\n❌ 状态：**Gateway 宕机，恢复失败！**")
    elif status == "ISSUES_FOUND":
        lines.append(f"\n⚠️ 状态：**发现问题**（建议检查）")
    elif status == "AUTO_REPAIRED":
        lines.append(f"\n🔧 状态：**已自动修复**")

    return "\n".join(lines)


def send_feishu_notification(message: str):
    """推送飞书通知"""
    try:
        import yaml
        config_path = Path.home() / ".config" / "hongjun" / "config.yaml"
        if not config_path.exists():
            return
        config = yaml.safe_load(config_path.read_text())
        feishu = config.get("feishu", {})
        app_id = feishu.get("app_id", "")
        app_secret = feishu.get("app_secret", "")
        if not app_id or not app_secret:
            return

        import httpx
        # 获取 tenant_access_token
        token_resp = httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        token_data = token_resp.json()
        token = token_data.get("tenant_access_token", "")

        # 发送到朝堂群
        chat_id = "oc_d860f9f653e3421db6ea419a81414cf6"
        msg_resp = httpx.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": message}),
            },
            timeout=10,
        )
        logger.info(f"飞书通知发送: {msg_resp.status_code}")
    except Exception as e:
        logger.error(f"飞书通知发送失败: {e}")


def daemon_mode(interval_minutes: int = 30):
    """守护进程模式：定期自检"""
    import time
    logger.info(f"自检守护进程启动（每 {interval_minutes} 分钟一次）")
    while True:
        try:
            report = run_check(fix=True)
            if report["status"] != "OK":
                msg = format_report(report)
                send_feishu_notification(msg)
            else:
                logger.info("自检完成，无异常")
        except Exception as e:
            logger.error(f"自检失败: {e}")
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hongjun 自检")
    parser.add_argument("--fix", action="store_true", help="自动修复发现的问题")
    parser.add_argument("--daemon", action="store_true", help="守护进程模式")
    parser.add_argument("--interval", type=int, default=30, help="守护进程检查间隔（分钟）")
    args = parser.parse_args()

    if args.daemon:
        daemon_mode(interval_minutes=args.interval)
    else:
        report = run_check(fix=args.fix)
        print(format_report(report))
        if report["status"] != "OK" and not args.fix:
            print("\n（使用 --fix 自动修复）")
            sys.exit(1)
