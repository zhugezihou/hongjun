#!/usr/bin/env python3
"""
鸿钧 · 主入口
===============

六部尚书协同系统的核心调度层。

工作流程：
  用户请求
    ↓
  [吏部·协调]  意图解析 + 任务分解
    ↓
  [兵部·安全]  输入审核（必须第一步）
    ↓
  [户部·记忆]  注入相关记忆上下文
    ↓
  ┌─────────────────────────────────────┐
  │  [礼部·工具]  执行任务（浏览器/搜索） │
  │  [工部·执行]  代码生成/命令执行      │
  │  [刑部·评测]  质量评估（可选）       │
  └─────────────────────────────────────┘
    ↓
  [吏部·汇总]  整理结果
    ↓
  [户部·存储]  记住这次对话
    ↓
  返回用户

用法：
  # 方式1：直接调用
  from hongjun import Hongjun
  hongjun = Hongjun()
  response = hongjun.chat("帮我搜索 GitHub 今天的 AI 趋势")

  # 方式2：命令行
  python3 hongjun.py "帮我搜索 GitHub 今天的 AI 趋势"

  # 方式3：启动 Web 服务
  python3 hongjun.py --serve --port 20030
"""

import sys
import os
import time
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# === 导入核心模块 ===
from hongjun.orchestrator import process_request
from hongjun.memory import HongjunMemory
from hongjun.executor import HongjunExecutor
from hongjun.security import HongjunSecurity, Permission, PermissionGuard
from hongjun.evaluator import HongjunEvaluator


# === 鸿钧主类 ===

class Hongjun:
    """
    鸿钧 · 六部尚书协同系统主类

    整合六部，提供统一对话接口。
    """

    def __init__(
        self,
        user_id: str = "default",
        config: Optional[Dict[str, Any]] = None,
        enable_security: bool = True,
        enable_memory: bool = True,
        enable_eval: bool = True,
    ):
        self.user_id = user_id
        self.config = config or {}
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 各部实例
        self.memory = HongjunMemory(user_id=user_id) if enable_memory else None
        self.executor = HongjunExecutor()
        self.security = HongjunSecurity() if enable_security else None
        self.evaluator = HongjunEvaluator() if enable_eval else None
        self.permission = PermissionGuard(user_level=Permission.USER)

        # 统计
        self.stats = {
            "total_requests": 0,
            "total_tokens_approx": 0,
            "avg_response_time_ms": 0,
        }

        print(f"[鸿钧] 启动 | 会话: {self.session_id} | 用户: {user_id}")

    def chat(
        self,
        user_message: str,
        return_eval_report: bool = False,
    ) -> Dict[str, Any]:
        """
        统一对话接口

        Args:
            user_message: 用户消息
            return_eval_report: 是否返回评测报告

        Returns:
            {
                "response": str,          # 鸿钧的回复
                "eval_report": dict?,     # 可选：评测报告
                "stats": dict,            # 统计信息
            }
        """
        start_ms = time.time() * 1000
        request_id = str(uuid.uuid4())[:8]

        print(f"\n[鸿钧·{request_id}] 📥 用户: {user_message[:80]}")

        # === 步骤1：兵部安全审核 ===
        if self.security:
            passed, error = self.security.check_input(user_message)
            if not passed:
                result = {
                    "response": f"❌ 兵部拦截：{error}",
                    "stats": self._update_stats(start_ms),
                }
                if return_eval_report:
                    result["eval_report"] = None
                return result
            print(f"[鸿钧·{request_id}] ✅ 兵部安全审核通过")

        # === 步骤2：户部记忆注入 ===
        memory_context = ""
        if self.memory:
            memory_context = self.memory.build_context(user_message)
            if memory_context:
                print(f"[鸿钧·{request_id}] 💾 户部记忆已注入 ({len(memory_context)} chars)")

        # === 步骤3：吏部协调（核心流程）===
        # 吏部内部已经集成了：分解 → 分发 → 汇总
        # 这里我们直接调用，让它使用上面的 memory_context
        # 由于吏部模块已经内置了户部调用，我们直接用它
        try:
            raw_response = process_request(user_message)
        except Exception as e:
            raw_response = f"⚠️ 处理异常：{e}"

        # === 步骤4：刑部评测 ===
        eval_report = None
        if self.evaluator:
            execution_time_ms = time.time() * 1000 - start_ms
            eval_report = self.evaluator.evaluate(
                task=user_message,
                result=raw_response,
                execution_time_ms=execution_time_ms,
            )
            print(f"[鸿钧·{request_id}] 📊 刑部评分: {eval_report.overall_score:.0%} ({eval_report.grade})")

        # === 步骤5：户部存储记忆 ===
        if self.memory and raw_response and not raw_response.startswith("❌"):
            self.memory.remember(
                content=f"用户问：{user_message}\n鸿钧答：{raw_response[:500]}",
                importance=0.7,
                tags=["对话"],
            )

        # === 步骤6：组装最终回复 ===
        response_parts = [raw_response]

        if eval_report and eval_report.overall_score < 0.7:
            response_parts.append(
                f"\n\n⚠️ 刑部提醒：本次回答质量评分 {eval_report.overall_score:.0%}，"
                f"建议复核关键信息。"
            )

        final_response = "\n".join(response_parts)

        print(f"[鸿钧·{request_id}] 📤 回复 ({len(final_response)} chars, "
              f"{time.time()*1000-start_ms:.0f}ms)")

        result = {
            "response": final_response,
            "stats": self._update_stats(start_ms),
        }

        if return_eval_report and eval_report:
            result["eval_report"] = {
                "score": eval_report.overall_score,
                "grade": eval_report.grade,
                "dimensions": eval_report.dimensions,
                "warnings": eval_report.warnings,
            }

        return result

    def _update_stats(self, start_ms: float) -> Dict[str, Any]:
        """更新统计信息"""
        elapsed = time.time() * 1000 - start_ms
        self.stats["total_requests"] += 1
        n = self.stats["total_requests"]
        old_avg = self.stats["avg_response_time_ms"]
        self.stats["avg_response_time_ms"] = (old_avg * (n - 1) + elapsed) / n
        return dict(self.stats)

    def status(self) -> str:
        """查看鸿钧系统状态"""
        lines = [
            f"鸿钧系统状态 | 会话: {self.session_id}",
            "=" * 40,
            f"用户: {self.user_id}",
            f"请求数: {self.stats['total_requests']}",
            f"平均响应: {self.stats['avg_response_time_ms']:.0f}ms",
            "",
            "组件状态:",
        ]

        if self.security:
            lines.append("  ✅ 兵部·安全护栏")
        if self.memory:
            lines.append("  ✅ 户部·记忆系统")
        if self.evaluator:
            lines.append("  ✅ 刑部·质量评估")

        if self.memory:
            lines.append("")
            lines.append("记忆状态:")
            for line in self.memory.status().split("\n"):
                lines.append(f"  {line}")

        return "\n".join(lines)


# === 命令行接口 ===

def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="鸿钧 · 六部尚书协同系统")
    parser.add_argument("message", nargs="?", help="用户消息")
    parser.add_argument("--user-id", default="cli", help="用户 ID")
    parser.add_argument("--serve", action="store_true", help="启动 Web 服务")
    parser.add_argument("--port", type=int, default=20030, help="Web 服务端口")
    parser.add_argument("--status", action="store_true", help="查看系统状态")
    parser.add_argument("--no-security", action="store_true", help="禁用安全审核")
    parser.add_argument("--no-memory", action="store_true", help="禁用记忆系统")
    parser.add_argument("--no-eval", action="store_true", help="禁用质量评估")

    args = parser.parse_args()

    hongjun = Hongjun(
        user_id=args.user_id,
        enable_security=not args.no_security,
        enable_memory=not args.no_memory,
        enable_eval=not args.no_eval,
    )

    if args.status:
        print(hongjun.status())
        return

    if args.serve:
        _start_server(hongjun, args.port)
        return

    if args.message:
        result = hongjun.chat(args.message)
        print()
        print("=" * 50)
        print(result["response"])
        return

    # 交互模式
    print("鸿钧 · 六部尚书协同系统")
    print("输入消息开始对话，输入 status 查看状态，输入 quit 退出")
    print()

    while True:
        try:
            user_input = input("📝 你: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["quit", "exit", "q"]:
                print("👋 鸿钧告辞")
                break
            if user_input.lower() == "status":
                print(hongjun.status())
                continue

            result = hongjun.chat(user_input)
            print()
            print("=" * 50)
            print(result["response"])
            print()
        except KeyboardInterrupt:
            print("\n👋 鸿钧告辞")
            break
        except Exception as e:
            print(f"❌ 异常: {e}")


def _start_server(hongjun: Hongjun, port: int):
    """启动简单 HTTP 服务"""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import json
    import threading

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # 静默日志

        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "service": "hongjun"}).encode())
            elif self.path == "/status":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(hongjun.status().encode())
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/chat":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                try:
                    data = json.loads(body.decode())
                    message = data.get("message", "")
                    result = hongjun.chat(message)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
                except Exception as e:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode())
            else:
                self.send_response(404)
                self.end_headers()

    server = HTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"🌐 鸿钧 Web 服务已启动: http://0.0.0.0:{port}")
    print("   GET  /health     — 健康检查")
    print("   GET  /status     — 系统状态")
    print("   POST /chat       — 对话接口 (JSON: {'message': '...'})")

    # 保持主线程
    try:
        thread.join()
    except KeyboardInterrupt:
        print("\n👋 服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
