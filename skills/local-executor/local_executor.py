#!/usr/bin/env python3
"""
local-executor skill — 本地机器执行能力
"""
import subprocess
import os
import re
from pathlib import Path

WORKDIR = os.path.expanduser("~")

# ── Secret Redaction ──────────────────────────────────────────────────────────
# 与 hongjun.logging_config 保持一致的脱敏模式

_SECRET_RE = [
    (re.compile(r"(bearer\s+)[a-zA-Z0-9\-._~+/+=!@#$%^&*()]+", re.I), r"\1[REDACTED]"),
    (re.compile(r"(sk-[a-zA-Z0-9\-]{20,})"), "[REDACTED_API_KEY]"),
    (re.compile(r"(ghp_[a-zA-Z0-9]{36})"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(gho_[a-zA-Z0-9]{36})"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(gnupg|gpg)_signing_key\s*=\s*[a-zA-Z0-9+/=]{20,}"), "[REDACTED_GPG_KEY]"),
    (re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----.*?-----END\s+(?:RSA\s+)?PRIVATE\s+KEY-----", re.DOTALL), "[REDACTED_PEM_KEY]"),
    (re.compile(r"([A-Z_]{3,20}=(?:bearer|token|key|secret|password|passwd|pwd)[a-zA-Z0-9\-._~+/]{8,})", re.I), "[REDACTED_ENV]"),
    (re.compile(r"(\b[a-zA-Z0-9+/=]{40,}\b)"), "[REDACTED_LIKELY_KEY]"),  # long base64-like strings
]


def _sanitize_cmd(cmd: str) -> str:
    """脱敏命令字符串中的 secret（不改变长度，防止 command string alignment 泄露）"""
    for pattern, replacement in _SECRET_RE:
        cmd = pattern.sub(replacement, cmd)
    return cmd


def _safe_log(cmd: str) -> None:
    """打印命令（脱敏后），不打印敏感信息"""
    import sys
    safe = _sanitize_cmd(cmd)
    print(f"[CMD] {safe}", file=sys.stderr)



def run_command(cmd: str) -> str:
    """执行任意 shell 命令"""
    _safe_log(cmd)  # 脱敏后的命令（不暴露 secrets）
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=120, cwd=WORKDIR
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode == 0:
            return out or "(命令成功执行，无输出)"
        else:
            return f"[Exit {result.returncode}] {_sanitize_cmd(err or out)}"
    except subprocess.TimeoutExpired:
        return "[超时] 命令执行超过 120 秒"
    except Exception as e:
        return f"[错误] {e}"


def write_file(path: str, content: str) -> str:
    """写或覆盖文件"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"✅ 已写入: {path} ({len(content)} 字节)"
    except Exception as e:
        return f"[错误] 无法写入 {path}: {e}"


def read_file(path: str) -> str:
    """读文件内容"""
    try:
        p = Path(path)
        if not p.exists():
            return f"[错误] 文件不存在: {path}"
        content = p.read_text(encoding="utf-8")
        return content[:8000]  # 限制输出长度
    except Exception as e:
        return f"[错误] 无法读取 {path}: {e}"


def git_status(path: str = WORKDIR) -> str:
    """查 git 仓库状态"""
    try:
        result = subprocess.run(
            ["git", "status", "--short"], capture_output=True, text=True, cwd=path
        )
        out = result.stdout.strip()
        if not out:
            return "✅ 工作区干净"
        return f"📋 {path} 工作区状态:\n{out}"
    except Exception as e:
        return f"[错误] git status: {e}"


def git_commit(message: str, files: list = None, path: str = WORKDIR) -> str:
    """提交文件"""
    try:
        if files:
            subprocess.run(["git", "add"] + files, cwd=path, check=True)
        else:
            subprocess.run(["git", "add", "-A"], cwd=path, check=True)
        result = subprocess.run(
            ["git", "commit", "-m", message], capture_output=True, text=True, cwd=path
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode == 0:
            return out or "✅ 提交成功"
        else:
            return f"[提交失败] {err or out}"
    except Exception as e:
        return f"[错误] git commit: {e}"


def git_push(path: str = WORKDIR) -> str:
    """推送当前分支"""
    try:
        result = subprocess.run(
            ["git", "push"], capture_output=True, text=True, cwd=path
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode == 0:
            return out or "✅ 推送成功"
        else:
            return f"[推送失败] {err or out}"
    except Exception as e:
        return f"[错误] git push: {e}"


def git_branch(name: str, path: str = WORKDIR) -> str:
    """创建分支"""
    try:
        subprocess.run(["git", "checkout", "-b", name], cwd=path,
                       capture_output=True, check=True)
        return f"✅ 已创建并切换到分支: {name}"
    except Exception as e:
        return f"[错误] git branch: {e}"


def git_checkout(name: str, path: str = WORKDIR) -> str:
    """切换分支"""
    try:
        subprocess.run(["git", "checkout", name], cwd=path,
                       capture_output=True, check=True)
        return f"✅ 已切换到分支: {name}"
    except Exception as e:
        return f"[错误] git checkout: {e}"


def systemctl(action: str, unit: str = "") -> str:
    """systemd 服务管理"""
    if action == "status":
        cmd = ["systemctl", "status", unit] if unit else ["systemctl", "status"]
    else:
        if not unit:
            return "[错误] 需要指定服务名 (unit)"
        cmd = ["sudo", "systemctl", action, unit]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode == 0 or action == "status":
            return out or f"✅ {action} {unit} 成功"
        else:
            return f"[失败] {err or out}"
    except Exception as e:
        return f"[错误] systemctl: {e}"


def process_list(name_filter: str = "") -> str:
    """列出进程"""
    try:
        if name_filter:
            cmd = ["ps", "aux"] if not name_filter else ["ps", "aux"] | ["grep", name_filter]
            result = subprocess.run("ps aux | grep -i '" + name_filter + "'",
                                    shell=True, capture_output=True, text=True)
        else:
            result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
        out = result.stdout.strip()
        lines = out.split("\n")
        if len(lines) > 20:
            return "\n".join(lines[:20]) + f"\n...共 {len(lines)} 行"
        return out
    except Exception as e:
        return f"[错误] process_list: {e}"


def process_kill(pid: int) -> str:
    """杀进程"""
    try:
        subprocess.run(["kill", str(pid)], check=True)
        return f"✅ 已杀死进程 {pid}"
    except Exception as e:
        return f"[错误] 无法杀死 {pid}: {e}"


_process_log = {}

def process_start(cmd: str, workdir: str = WORKDIR) -> str:
    """后台启动进程"""
    import time
    safe_cmd = _sanitize_cmd(cmd)
    _safe_log(cmd)  # 脱敏后打印
    try:
        proc = subprocess.Popen(
            cmd, shell=True, cwd=workdir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        pid = proc.pid
        _process_log[pid] = {"cmd": safe_cmd, "workdir": workdir, "started": time.time()}  # 存脱敏版本
        time.sleep(0.5)
        # Check if still running
        if proc.poll() is None:
            return f"✅ 已启动 (PID: {pid}): {safe_cmd}"
        else:
            return f"[启动后立即退出] PID: {pid}"
    except Exception as e:
        return f"[错误] process_start: {e}"


def deploy_script(script_path: str, workdir: str = WORKDIR) -> str:
    """执行部署脚本"""
    if not Path(script_path).exists():
        return f"[错误] 脚本不存在: {script_path}"
    try:
        result = subprocess.run(
            ["bash", script_path], capture_output=True, text=True,
            cwd=workdir, timeout=300
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode == 0:
            return out or "✅ 脚本执行成功"
        else:
            return f"[Exit {result.returncode}] {err or out}"
    except subprocess.TimeoutExpired:
        return "[超时] 脚本执行超过 5 分钟"
    except Exception as e:
        return f"[错误] deploy_script: {e}"


# === Skill 元信息 ===
SKILL_METADATA = {
    "name": "local-executor",
    "version": "v1.0",
    "description": "本地机器执行能力：文件/git/服务/进程/部署",
    "functions": {
        "run_command": run_command,
        "write_file": write_file,
        "read_file": read_file,
        "git_status": git_status,
        "git_commit": git_commit,
        "git_push": git_push,
        "git_branch": git_branch,
        "git_checkout": git_checkout,
        "systemctl": systemctl,
        "process_list": process_list,
        "process_kill": process_kill,
        "process_start": process_start,
        "deploy_script": deploy_script,
    }
}

if __name__ == "__main__":
    # 简单测试
    print(git_status())
