"""
鸿钧 · 记忆注入系统
====================

每次 LLM 调用前，自动注入相关记忆上下文。

三层记忆注入：
  1. 身份记忆（L0）：鸿钧的身份定位，始终注入
  2. 经验模式（L1）：反思引擎的经验模式（best strategy）
  3. 近期记忆（L2）：evolution_memory 中与当前任务相关的记录

注入位置：LLM 调用前的 messages 列表最前面（system 消息之后）

使用方式：
    injector = MemoryInjector()
    messages = injector.inject(messages, user_request, intent_type)
    # messages 已经被注入了 [记忆上下文] 的 system 消息
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.memory_injection")

# 身份记忆文件（始终注入）
IDENTITY_FILE = Path(__file__).parent.parent.parent / "data" / "mempalace_identity.txt"
DEFAULT_IDENTITY = """[identity] 鸿钧：独立 AI Agent，能理解意图、拆解任务、验证结果、主动学习
[capability] 任务分解执行、代码生成、网页搜索、记忆管理、自我修复
[wing/hongjun] 六部：Coordinator/Executor/Memory/Tools/Security/Evaluator
[evolution] 目标：持续自我进化，成为能发现更厉害项目并学习进化的 Agent
[not_shu_liu_bu] 不是外部六部协调系统，是独立的 Agent"""


class MemoryInjector:
    """
    记忆注入器：在每次 LLM 调用前注入相关记忆。

    使用方式：
        injector = MemoryInjector()
        enriched_messages = injector.inject(
            messages=original_messages,
            user_request="帮我写一个快排",
            intent_type="code_generation",
        )
    """

    # 记忆注入的系统消息标记（用于检测和去重）
    MEMORY_INJECTION_MARKER = "[HongjunMemoryContext]"

    def __init__(self):
        self._identity_cache: Optional[str] = None

    # ── 入口 ────────────────────────────────────────────────────────────

    def inject(
        self,
        messages: list[dict],
        user_request: str,
        intent_type: str = "",
    ) -> list[dict]:
        """
        注入记忆上下文到 messages 列表。

        Args:
            messages: 原始 messages 列表
            user_request: 用户原始请求（用于检索相关记忆）
            intent_type: 意图类型（可选）

        Returns:
            注入了记忆上下文的 messages 列表
        """
        if not messages:
            return messages

        # 构建记忆上下文
        memory_context = self._build_context(user_request, intent_type)
        if not memory_context:
            return messages  # 无记忆，不注入

        # 检查是否已有标记（避免重复注入）
        if any(
            isinstance(m.get("content"), str) and self.MEMORY_INJECTION_MARKER in m.get("content", "")
            for m in messages
        ):
            return messages

        # 注入到 system 消息之后（第一个非 system 消息之前）
        injection_msg = {
            "role": "system",
            "content": f"{self.MEMORY_INJECTION_MARKER}\n{memory_context}",
        }

        # 找第一个非 system 消息的位置
        insert_idx = 0
        for i, m in enumerate(messages):
            if m.get("role") != "system":
                insert_idx = i
                break
        else:
            insert_idx = len(messages)

        enriched = messages.copy()
        enriched.insert(insert_idx, injection_msg)
        logger.debug(f"记忆注入完成：{len(memory_context)} chars")
        return enriched

    def _build_context(self, user_request: str, intent_type: str) -> str:
        """
        构建完整的记忆上下文字符串。
        """
        parts = []

        # 1. 身份记忆（始终注入）
        identity = self._get_identity()
        if identity:
            parts.append(f"[身份]\n{identity}")

        # 2. 经验模式（如果 intent_type 明确）
        if intent_type:
            best_strategy = self._get_best_strategy(intent_type, user_request)
            if best_strategy:
                parts.append(f"[相关经验]\n{best_strategy}")

        # 3. 近期相关记忆（来自 evolution_memory）
        recent_memories = self._get_recent_memories(user_request)
        if recent_memories:
            parts.append(f"[近期经验]\n{recent_memories}")

        if not parts:
            return ""

        return "\n\n".join(parts)

    # ── 身份记忆 ────────────────────────────────────────────────────────

    def _get_identity(self) -> str:
        """获取身份记忆（带缓存）"""
        if self._identity_cache is not None:
            return self._identity_cache

        if IDENTITY_FILE.exists():
            try:
                self._identity_cache = IDENTITY_FILE.read_text(encoding="utf-8").strip()
                return self._identity_cache
            except Exception:
                pass

        self._identity_cache = DEFAULT_IDENTITY
        return DEFAULT_IDENTITY

    def refresh_identity(self):
        """刷新身份缓存（当身份文件更新时调用）"""
        self._identity_cache = None

    # ── 经验模式 ────────────────────────────────────────────────────────

    def _get_best_strategy(self, intent_type: str, user_request: str) -> Optional[str]:
        """从反思引擎获取最佳策略"""
        try:
            from hongjun.reflection_engine import get_reflection_engine
            engine = get_reflection_engine()
            strategy = engine.get_best_strategy(intent_type, user_request)
            return strategy
        except Exception as e:
            logger.warning(f"获取最佳策略失败: {e}")
            return None

    # ── 近期记忆 ────────────────────────────────────────────────────────

    def _get_recent_memories(self, user_request: str) -> Optional[str]:
        """从 evolution_memory 获取与当前任务相关的近期记忆"""
        try:
            from hongjun.evolution_memory import EvolutionMemory
            mem = EvolutionMemory()
            results = mem.search(user_request, limit=3)
            if not results:
                return None

            lines = []
            for r in results:
                t = r.get("type", "")
                if t == "success":
                    lines.append(f"✅ {r.get('task', '')}: {r.get('result_preview', '')[:100]}")
                elif t == "failure":
                    fix = r.get("fix_applied", "")
                    lines.append(f"❌ {r.get('error', '')[:100]}" + (f" | 已修复: {fix[:50]}" if fix else ""))
            return "\n".join(lines) if lines else None
        except Exception as e:
            logger.warning(f"获取近期记忆失败: {e}")
            return None


# ── 全局单例 ────────────────────────────────────────────────────────────────

_memory_injector: Optional[MemoryInjector] = None


def get_memory_injector() -> MemoryInjector:
    global _memory_injector
    if _memory_injector is None:
        _memory_injector = MemoryInjector()
    return _memory_injector
