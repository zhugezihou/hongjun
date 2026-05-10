"""
鸿钧 · 进化记忆系统
====================

记录每次任务执行的经验，形成持续进化的知识库。

记录内容：
  - 成功的任务模式（请求 → 行为 → 结果）
  - 失败的模式（什么导致失败、如何修复）
  - 自我修复历史（发现了什么 bug、如何修）

查询方式：
  - 按关键词检索相关经验
  - 按时间线回顾进化历史
  - 统计各模块出问题频率

数据存储：
  - ~/.hongjun/evolution_memory.json
  - 每次启动自动加载
  - 每次任务结束后自动写入
"""

from __future__ import annotations
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.evolution_memory")

MEMORY_DIR = Path.home() / ".hongjun"
MEMORY_FILE = MEMORY_DIR / "evolution_memory.json"


class EvolutionMemory:
    """
    进化记忆管理器。

    使用方式：
        mem = EvolutionMemory()
        mem.record_success(task="开发矩阵动画", request="...", result="截图路径")
        mem.record_failure(task="开发矩阵动画", error="语法错误", fix="...")
        results = mem.search("矩阵动画")
    """

    def __init__(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    # ── 持久化 ────────────────────────────────────────────────────

    def _load(self) -> dict:
        """加载记忆"""
        if not MEMORY_FILE.exists():
            return self._empty_memory()
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return self._empty_memory()

    def _save(self):
        """保存记忆"""
        try:
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存进化记忆失败: {e}")

    def _empty_memory(self) -> dict:
        return {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "successes": [],      # 成功记录
            "failures": [],       # 失败记录（带修复方案）
            "self_repairs": [],   # 自我修复记录
            "skill_patterns": [],  # 技能模式
            "stats": {            # 统计
                "total_tasks": 0,
                "successes": 0,
                "failures": 0,
                "self_repairs": 0,
            },
        }

    # ── 记录 ──────────────────────────────────────────────────────

    def record_success(
        self,
        task: str,
        request: str,
        result: str,
        execution_time: float = 0,
        intent: str = "",
        modules_used: list[str] = None,
    ):
        """
        记录一次成功的任务执行。
        """
        entry = {
            "id": f"success_{int(time.time() * 1000)}",
            "task": task,
            "request": request[:500],
            "result_preview": result[:500],
            "execution_time": execution_time,
            "intent": intent,
            "modules_used": modules_used or [],
            "timestamp": datetime.now().isoformat(),
        }
        self.data["successes"].insert(0, entry)  # 最新在前
        self.data["successes"] = self.data["successes"][:500]  # 最多500条
        self.data["stats"]["total_tasks"] += 1
        self.data["stats"]["successes"] += 1
        self._save()

        # 提取技能模式
        self._extract_skill_pattern(task, request, intent, modules_used)

    def record_failure(
        self,
        task: str,
        request: str,
        error: str,
        error_type: str = "",
        fix_applied: str = "",
        modules_involved: list[str] = None,
    ):
        """
        记录一次失败的任务执行。
        """
        entry = {
            "id": f"failure_{int(time.time() * 1000)}",
            "task": task,
            "request": request[:500],
            "error": error[:500],
            "error_type": error_type,
            "fix_applied": fix_applied[:500] if fix_applied else "",
            "modules_involved": modules_involved or [],
            "timestamp": datetime.now().isoformat(),
            "resolved": bool(fix_applied),
        }
        self.data["failures"].insert(0, entry)
        self.data["failures"] = self.data["failures"][:500]
        self.data["stats"]["total_tasks"] += 1
        self.data["stats"]["failures"] += 1
        self._save()

    def record_self_repair(
        self,
        module: str,
        error: str,
        fix_description: str,
        fix_applied: bool,
        verification_passed: bool,
    ):
        """
        记录一次自我修复。
        """
        entry = {
            "id": f"repair_{int(time.time() * 1000)}",
            "module": module,
            "error": error[:300],
            "fix_description": fix_description[:300],
            "fix_applied": fix_applied,
            "verification_passed": verification_passed,
            "timestamp": datetime.now().isoformat(),
        }
        self.data["self_repairs"].insert(0, entry)
        self.data["self_repairs"] = self.data["self_repairs"][:200]
        if fix_applied:
            self.data["stats"]["self_repairs"] += 1
        self._save()

    def _extract_skill_pattern(
        self, task: str, request: str, intent: str, modules_used: list[str] = None
    ):
        """从成功案例中提取技能模式"""
        # 简单规则提取
        keywords = []
        if any(k in task.lower() for k in ["动画", "游戏", "可视化", "canvas", "webgl"]):
            keywords.append("visual")
        if any(k in task.lower() for k in ["搜索", "查询", "了解"]):
            keywords.append("search")
        if any(k in task.lower() for k in ["开发", "写代码", "实现", "生成"]):
            keywords.append("code_generation")
        if any(k in task.lower() for k in ["飞书", "telegram", "通知"]):
            keywords.append("messaging")

        for kw in keywords:
            existing = [p for p in self.data["skill_patterns"] if p["keyword"] == kw]
            if existing:
                existing[0]["count"] = existing[0].get("count", 0) + 1
            else:
                self.data["skill_patterns"].append({
                    "keyword": kw,
                    "count": 1,
                    "example_request": request[:200],
                    "modules_used": modules_used or [],
                    "last_seen": datetime.now().isoformat(),
                })

    # ── 查询 ──────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """
        搜索相关记忆。
        """
        query_lower = query.lower()
        results = []
        for s in self.data["successes"]:
            if (query_lower in s.get("task", "").lower() or
                query_lower in s.get("request", "").lower() or
                query_lower in s.get("intent", "").lower()):
                results.append({"type": "success", **s})
            if len(results) >= limit:
                break
        for f in self.data["failures"]:
            if (query_lower in f.get("task", "").lower() or
                query_lower in f.get("error", "").lower()):
                results.append({"type": "failure", **f})
            if len(results) >= limit:
                break
        return results[:limit]

    def get_recent_failures(self, limit: int = 10) -> list[dict]:
        """获取最近的失败记录"""
        return self.data["failures"][:limit]

    def get_failed_modules(self) -> dict[str, int]:
        """统计各模块失败次数"""
        freq = {}
        for f in self.data["failures"]:
            for m in f.get("modules_involved", []):
                freq[m] = freq.get(m, 0) + 1
        return dict(sorted(freq.items(), key=lambda x: x[1], reverse=True))

    def get_stats(self) -> dict:
        """获取统计信息"""
        stats = self.data["stats"].copy()
        stats["success_rate"] = (
            round(stats["successes"] / stats["total_tasks"] * 100, 1)
            if stats["total_tasks"] > 0 else 0
        )
        stats["top_patterns"] = sorted(
            self.data["skill_patterns"],
            key=lambda x: x.get("count", 0),
            reverse=True,
        )[:5]
        return stats

    def build_context(self, query: str, max_memories: int = 5) -> str:
        """
        根据查询构建记忆上下文，用于给 LLM 提供背景知识。
        """
        memories = self.search(query, limit=max_memories)
        if not memories:
            return ""

        lines = ["[HongjunEvolutionMemory]"]
        for m in memories:
            t = m.get("type", "")
            if t == "success":
                lines.append(f"✅ 成功: {m.get('task', '')} → {m.get('result_preview', '')[:100]}")
            elif t == "failure":
                fix = m.get("fix_applied", "")
                lines.append(f"❌ 失败: {m.get('error', '')[:100]}" +
                             (f" | 修复: {fix[:80]}" if fix else ""))
        lines.append("[/HongjunEvolutionMemory]\n")
        return "\n".join(lines)

    # ── 自动调用钩子 ──────────────────────────────────────────────

    def on_task_complete(self, request: str, result: str, intent: str = "", execution_time: float = 0):
        """任务完成时自动调用"""
        self.record_success(
            task=intent or request[:50],
            request=request,
            result=result,
            execution_time=execution_time,
            intent=intent,
        )

    def on_task_failure(self, request: str, error: str, fix: str = ""):
        """任务失败时自动调用"""
        self.record_failure(
            task=request[:50],
            request=request,
            error=error,
            fix_applied=fix,
        )
