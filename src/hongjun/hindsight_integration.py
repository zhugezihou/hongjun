"""
鸿钧 · Hindsight 记忆集成
=========================

Hindsight (https://github.com/vectorize-io/hindsight) 是独立的 Agent 记忆服务。
提供 retain/recall/reflect 三个核心操作，以及四网络记忆架构：
  𝒲 (World)      — 客观世界事实
  ℬ (Beliefs)    — Agent 经验和信念
  𝒮 (Summaries)  — 合成的实体摘要
  𝒪 (Observations) — 观察和反思

集成方式：
  1. Hindsight Cloud（推荐）— 在 ui.hindsight.vectorize.io 注册获取 API key
  2. Local Docker — docker run ghcr.io/vectorize-io/hindsight
  3. Local Embedded — hindsight-all-slim（需 sentence-transformers）

用法：
  # 设置 API key
  export HINDSIGHT_API_KEY=your_key_here

  # 在鸿钧中使用
  from hongjun.hindsight_integration import HindsightIntegration
  hi = HindsightIntegration()
  hi.retain("用户喜欢中文回答", context="user_preference")
  result = hi.recall("用户的语言偏好是什么")
  reflection = hi.reflect("用户有什么特征？给我一个总结")
"""

from __future__ import annotations

import os
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from hindsight_client import Hindsight as HindsightClient

from hongjun.llm import chat_sync, LLMResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hindsight Client（lazy load）
# ---------------------------------------------------------------------------

_hindsight_client: Optional["HindsightClient"] = None
_hindsight_available: Optional[bool] = None


def _get_hindsight_client() -> Optional["HindsightClient"]:
    """懒加载 Hindsight client，失败返回 None（不阻塞主流程）。"""
    global _hindsight_client, _hindsight_available

    if _hindsight_available is False:
        return None
    if _hindsight_client is not None:
        return _hindsight_client

    try:
        from hindsight_client import Hindsight as HindsightClient

        api_key = os.environ.get("HINDSIGHT_API_KEY", "")
        if not api_key:
            logger.debug("HINDSIGHT_API_KEY not set, Hindsight 不可用")
            _hindsight_available = False
            return None

        _hindsight_client = HindsightClient(
            base_url="https://api.hindsight.vectorize.io",
            api_key=api_key,
        )
        _hindsight_available = True
        logger.info("Hindsight Client 已连接（Cloud 模式）")
        return _hindsight_client
    except ImportError:
        logger.debug("hindsight-client 未安装，Hindsight 不可用")
        _hindsight_available = False
        return None
    except Exception as e:
        logger.warning(f"Hindsight Client 连接失败: {e}")
        _hindsight_available = False
        return None


# ---------------------------------------------------------------------------
# 工具函数：四网络分类
# ---------------------------------------------------------------------------

MEMORY_TYPE_DESCRIPTIONS = {
    "world":       "客观世界事实（可验证的信息）",
    "experience":  "Agent 亲身经历的事件",
    "opinion":     "Agent 的主观判断和观点",
    "observation": "原始观察和感受",
}


# ---------------------------------------------------------------------------
# HindsightIntegration 主类
# ---------------------------------------------------------------------------

class HindsightIntegration:
    """
    鸿钧的 Hindsight 记忆集成。

    提供三个核心操作：
      retain(content, context, memory_type) — 存入记忆
      recall(query, bank_id)               — 检索记忆
      reflect(query, bank_id)               — 深度反思

    四网络说明：
      world       — 𝒲 客观事实（"用户叫张明"）
      experience  — ℬ Agent 经验（"上次用户要求用英文回复"）
      opinion      — 𝒪 主观观点（"用户可能更喜欢简洁的回答"）
      observation  — 观察记录（"用户在凌晨2点提问，可能习惯夜猫子"）
    """

    DEFAULT_BANK_ID = "hongjun"
    DEFAULT_TIMEOUT = 120  # 秒

    def __init__(
        self,
        bank_id: str | None = None,
        mission: str | None = None,
        retain_mission: str | None = None,
    ):
        self.bank_id = bank_id or os.environ.get("HINDSIGHT_BANK_ID", self.DEFAULT_BANK_ID)
        self.mission = mission
        self.retain_mission = retain_mission

    # -------------------------------------------------------------------------
    # 公开 API
    # -------------------------------------------------------------------------

    def retain(
        self,
        content: str,
        *,
        context: str | None = None,
        memory_type: str | None = None,
        tags: list[str] | None = None,
        document_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict:
        """
        存入记忆到 Hindsight。

        Args:
            content: 要记忆的内容（自然语言）
            context: 简短标签，如 'user_preference', 'project_decision'
            memory_type: 记忆类型(world/experience/opinion/observation)
            tags: 自定义标签列表
            document_id: 关联的文档 ID
            timestamp: ISO 格式时间戳

        Returns:
            {"success": bool, "result": dict, "error": str|None}
        """
        client = _get_hindsight_client()
        if client is None:
            return {"success": False, "result": None, "error": "Hindsight 不可用"}

        try:
            import asyncio

            # 同步调用 async 方法
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                coro = client.aretain(
                    bank_id=self.bank_id,
                    content=content,
                    context=context,
                    tags=tags,
                    document_id=document_id,
                    timestamp=timestamp,
                )
                result = loop.run_until_complete(coro)
                logger.debug(f"Hindsight retain OK: {content[:50]}...")
                return {"success": True, "result": result, "error": None}
            finally:
                loop.close()

        except Exception as e:
            logger.warning(f"Hindsight retain 失败: {e}")
            return {"success": False, "result": None, "error": str(e)}

    def recall(
        self,
        query: str,
        *,
        bank_id: str | None = None,
        budget: str = "mid",
        max_results: int = 10,
    ) -> dict:
        """
        多策略检索记忆（向量 + BM25 + 图遍历 + 时间过滤）。

        Args:
            query: 搜索 query
            bank_id: 记忆库 ID（默认用 self.bank_id）
            budget: 检索深度 ('low'/'mid'/'high')
            max_results: 最大返回数

        Returns:
            {"success": bool, "results": list, "error": str|None}
        """
        client = _get_hindsight_client()
        if client is None:
            return {"success": False, "results": [], "error": "Hindsight 不可用"}

        try:
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                coro = client.arecall(
                    bank_id=bank_id or self.bank_id,
                    query=query,
                    budget=budget,
                    max_results=max_results,
                )
                result = loop.run_until_complete(coro)
                # 提取文本
                memories = []
                if hasattr(result, "results"):
                    for r in result.results:
                        mem = {
                            "text": getattr(r, "text", str(r)),
                            "memory_type": getattr(r, "memory_type", None),
                            "importance": getattr(r, "importance", None),
                            "created_at": getattr(r, "created_at", None),
                        }
                        memories.append(mem)
                elif isinstance(result, list):
                    for r in result:
                        memories.append({"text": str(r)})

                logger.debug(f"Hindsight recall OK: {len(memories)} results for '{query}'")
                return {"success": True, "results": memories, "error": None}
            finally:
                loop.close()

        except Exception as e:
            logger.warning(f"Hindsight recall 失败: {e}")
            return {"success": False, "results": [], "error": str(e)}

    def reflect(
        self,
        query: str,
        *,
        bank_id: str | None = None,
        mission: str | None = None,
    ) -> dict:
        """
        深度反思——对记忆库进行推理，生成有洞察的回答。

        这是 Hindsight 最核心的操作：不同于 recall 返回原始记忆片段，
        reflect 会综合所有记忆，形成有推理过程的分析和结论。

        Args:
            query: 反思问题
            bank_id: 记忆库 ID
            mission: 自定义任务描述（覆盖默认）

        Returns:
            {"success": bool, "text": str, "error": str|None}
        """
        client = _get_hindsight_client()
        if client is None:
            return {"success": False, "text": "", "error": "Hindsight 不可用"}

        try:
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                coro = client.areflect(
                    bank_id=bank_id or self.bank_id,
                    query=query,
                    mission=mission or self.mission,
                )
                result = loop.run_until_complete(coro)
                text = ""
                if hasattr(result, "text"):
                    text = result.text
                elif hasattr(result, "content"):
                    text = result.content
                elif isinstance(result, str):
                    text = result

                logger.debug(f"Hindsight reflect OK: '{query}' -> {len(text)} chars")
                return {"success": True, "text": text, "error": None}
            finally:
                loop.close()

        except Exception as e:
            logger.warning(f"Hindsight reflect 失败: {e}")
            return {"success": False, "text": "", "error": str(e)}

    # -------------------------------------------------------------------------
    # 鸿钧专用：自动决定 memory_type
    # -------------------------------------------------------------------------

    def auto_type_retain(
        self,
        content: str,
        *,
        context: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """
        自动判断记忆类型后存入。

        使用 LLM 判断内容属于哪种记忆网络：
          - 客观事实         → world
          - 亲身经历/事件    → experience
          - 主观判断/观点   → opinion
          - 观察/感受       → observation
        """
        memory_type = self._classify_memory_type(content)

        return self.retain(
            content=content,
            context=context,
            memory_type=memory_type,
            tags=tags,
        )

    def _classify_memory_type(self, content: str) -> str:
        """用 LLM 判断记忆属于哪个网络。"""
        prompt = f"""判断以下内容属于哪种记忆类型：

类型定义：
- world: 客观可验证的事实（"用户叫张明"，"项目使用 Python"）
- experience: Agent 亲身经历的事件（"上次用户让我用英文回复"）
- opinion: Agent 的主观判断（"用户可能更喜欢简洁的回答"）
- observation: 观察和感受（"用户在凌晨2点提问，可能习惯夜猫子"）

内容：{content}

只输出一个词：world / experience / opinion / observation"""

        try:
            resp: LLMResponse = chat_sync(
                messages=[{"role": "user", "content": prompt}],
                model="MiniMax-M2.7",
                temperature=0,
                max_tokens=20,
            )
            result = (resp.content or "").strip().lower()
            if result in ("world", "experience", "opinion", "observation"):
                return result
        except Exception:
            pass

        return "experience"  # 默认

    # -------------------------------------------------------------------------
    # 鸿钧自我反思：调用 reflect 更新自我认知
    # -------------------------------------------------------------------------

    def self_reflect(
        self,
        recent_memories: str,
        focus_question: str = "这次对话有什么值得记住的重要信息？",
    ) -> dict:
        """
        鸿钧的自我反思：用 Hindsight reflect 能力分析近期记忆，
        形成对用户和任务的深度理解。

        这替代了鸿钧原有的一部分 evaluator 逻辑。
        """
        query = f"""基于以下近期对话记忆：{recent_memories}

问题：{focus_question}"""

        return self.reflect(query=query)


# ---------------------------------------------------------------------------
# 鸿钧工具注册
# ---------------------------------------------------------------------------

HINDSIGHT_TOOLS = [
    {
        "name": "hindsight_retain",
        "description": "存入记忆到 Hindsight 长期记忆库。自动提取实体、关系、时间信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记忆的内容（自然语言描述）",
                },
                "context": {
                    "type": "string",
                    "description": "简短标签，如 'user_preference' / 'project_decision' / 'error_fix'",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["world", "experience", "opinion", "observation"],
                    "description": "记忆类型，不提供则自动判断",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "自定义标签",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "hindsight_recall",
        "description": "多策略检索 Hindsight 记忆（向量 + BM25 + 图遍历 + 时间过滤）。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索 query",
                },
                "budget": {
                    "type": "string",
                    "enum": ["low", "mid", "high"],
                    "default": "mid",
                    "description": "检索深度",
                },
                "max_results": {
                    "type": "integer",
                    "default": 10,
                    "description": "最大返回数",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "hindsight_reflect",
        "description": "深度反思：综合所有相关记忆，形成有推理的分析和结论。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "反思问题",
                },
            },
            "required": ["query"],
        },
    },
]


def register_hindsight_tools():
    """
    将 Hindsight 工具注册到鸿钧工具层。
    在 HongjunTools.__init__ 或 startup 时调用。
    """

    hi = HindsightIntegration()

    def retain_wrapper(content: str, context: str = None, memory_type: str = None, tags: list = None) -> str:
        r = hi.retain(content=content, context=context, memory_type=memory_type, tags=tags)
        if r["success"]:
            return f"已存入记忆: {content[:50]}..."
        return f"存入失败: {r['error']}"

    def recall_wrapper(query: str, budget: str = "mid", max_results: int = 10) -> str:
        r = hi.recall(query=query, budget=budget, max_results=max_results)
        if not r["success"]:
            return f"检索失败: {r['error']}"
        if not r["results"]:
            return "没有找到相关记忆。"
        lines = [f"找到 {len(r['results'])} 条记忆："]
        for m in r["results"][:5]:
            mem_type = f"[{m['memory_type']}]" if m.get("memory_type") else ""
            lines.append(f"  {mem_type} {m['text'][:100]}")
        return "\n".join(lines)

    def reflect_wrapper(query: str) -> str:
        r = hi.reflect(query=query)
        if r["success"]:
            return r["text"]
        return f"反思失败: {r['error']}"

    # 注册到工具层（如果工具层支持动态注册）
    try:
        from hongjun.tools import tool_registry
        tool_registry.register("hindsight_retain", retain_wrapper)
        tool_registry.register("hindsight_recall", recall_wrapper)
        tool_registry.register("hindsight_reflect", reflect_wrapper)
        logger.info("Hindsight 工具已注册")
    except Exception as e:
        logger.warning(f"Hindsight 工具注册失败（工具层可能不支持动态注册）: {e}")
