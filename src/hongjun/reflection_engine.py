"""
鸿钧 · 反思引擎
================

定期复盘任务经验：巩固正确经验，遗忘错误经验。

触发时机：
  1. 每次任务完成后 → 自动小规模反思
  2. 每天 09:00 → 全量复盘
  3. 同一类型任务连续失败3次 → 专项反思

反思操作：
  - 巩固：成功模式 → skill_patterns 权重提升
  - 遗忘：错误经验 → 降权，连续失效则淘汰
  - 提炼：从成功案例中提炼通用策略
  - 修正：从失败案例中修正错误假设

记忆生命周期：
  新经验(临时) → 短期(7天) → 长期(活跃保留，不活跃淘汰)
"""

from __future__ import annotations
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.reflection")

MEMORY_DIR = Path.home() / ".hongjun"
REFLECTION_LOG = MEMORY_DIR / "reflection_log.jsonl"
PATTERNS_FILE = MEMORY_DIR / "experience_patterns.json"


# ── 数据结构 ────────────────────────────────────────────────────────────────

class ExperiencePattern:
    """经验模式：某类任务的最佳执行策略"""

    def __init__(
        self,
        pattern_id: str,
        intent_type: str,
        description: str,
        strategy: str,           # 核心策略描述
        success_count: int = 0,
        failure_count: int = 0,
        consecutive_failures: int = 0,
        last_success: Optional[str] = None,
        last_failure: Optional[str] = None,
        is_active: bool = True,  # False = 已遗忘/淘汰
        weight: float = 1.0,     # 置信度权重
        examples: list[str] = None,
        created_at: Optional[str] = None,
    ):
        self.pattern_id = pattern_id
        self.intent_type = intent_type
        self.description = description
        self.strategy = strategy
        self.success_count = success_count
        self.failure_count = failure_count
        self.consecutive_failures = consecutive_failures
        self.last_success = last_success
        self.last_failure = last_failure
        self.is_active = is_active
        self.weight = weight
        self.examples = examples or []
        self.created_at = created_at or datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "intent_type": self.intent_type,
            "description": self.description,
            "strategy": self.strategy,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "consecutive_failures": self.consecutive_failures,
            "last_success": self.last_success,
            "last_failure": self.last_failure,
            "is_active": self.is_active,
            "weight": self.weight,
            "examples": self.examples,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExperiencePattern":
        return cls(**d)


class ReflectionResult:
    """反思结果"""

    def __init__(
        self,
        trigger: str,             # "task_complete" / "daily" / "consecutive_failure"
        patterns_touched: list[str],
        actions: list[str],       # "consolidated" / "forgotten" / "refined"
        summary: str,
    ):
        self.trigger = trigger
        self.patterns_touched = patterns_touched
        self.actions = actions
        self.summary = summary
        self.timestamp = datetime.now().isoformat()


# ── 反思引擎主类 ────────────────────────────────────────────────────────────

class ReflectionEngine:
    """
    反思引擎：定期复盘经验，优化策略。

    使用方式：
        engine = ReflectionEngine()
        # 小反思：每次任务完成后
        result = engine.reflect_on_task(success=True, intent_type="code_generation", task="写一个快排")
        # 全量反思：每天定时
        engine.daily_reflection()
    """

    # 遗忘阈值：连续失败这么多次就降权/淘汰
    CONSECUTIVE_FAILURE_THRESHOLD = 3
    # 权重衰减：每次失败权重 * this
    WEIGHT_DECAY_ON_FAILURE = 0.6
    # 权重提升：每次成功权重 * this
    WEIGHT_BOOST_ON_SUCCESS = 1.2
    # 最低权重：低于此值淘汰
    MIN_WEIGHT_THRESHOLD = 0.15

    def __init__(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.patterns: dict[str, ExperiencePattern] = {}
        self._load_patterns()

    # ── 持久化 ────────────────────────────────────────────────────────────

    def _load_patterns(self):
        if not PATTERNS_FILE.exists():
            return
        try:
            with open(PATTERNS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for p in data.get("patterns", []):
                self.patterns[p["pattern_id"]] = ExperiencePattern.from_dict(p)
            logger.info(f"加载了 {len(self.patterns)} 个经验模式")
        except Exception as e:
            logger.warning(f"加载经验模式失败: {e}")

    def _save_patterns(self):
        try:
            data = {
                "version": 1,
                "updated_at": datetime.now().isoformat(),
                "patterns": [p.to_dict() for p in self.patterns.values()],
            }
            with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存经验模式失败: {e}")

    def _log_reflection(self, result: ReflectionResult):
        """写反思日志"""
        try:
            with open(REFLECTION_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "trigger": result.trigger,
                    "patterns_touched": result.patterns_touched,
                    "actions": result.actions,
                    "summary": result.summary,
                    "timestamp": result.timestamp,
                }, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"写反思日志失败: {e}")

    # ── 核心反思操作 ──────────────────────────────────────────────────────

    def reflect_on_task(
        self,
        success: bool,
        intent_type: str,
        task: str,
        request: str,
        result_preview: str = "",
        error: str = "",
        modules_used: list[str] = None,
    ) -> ReflectionResult:
        """
        每次任务完成后的小反思。

        成功 → 找对应模式并巩固（权重提升）
        失败 → 找对应模式并警告（权重下降，连续失败3次则淘汰）
        无对应模式 → 新建模式
        """
        modules_used = modules_used or []
        actions = []

        # 找匹配的现有模式
        matched = self._find_matching_pattern(intent_type, task)

        if matched:
            # 更新已有模式
            if success:
                actions = self._consolidate_pattern(matched, result_preview)
            else:
                actions = self._weaken_pattern(matched, error)
        else:
            # 新建模式（无论成功失败）
            pattern_id = f"{intent_type}_{int(time.time() * 1000)}"
            is_successive_failure = False
            if not success:
                # 检查是否同类型任务连续失败（由调用方控制阈值）
                is_successive_failure = True

            new_pattern = ExperiencePattern(
                pattern_id=pattern_id,
                intent_type=intent_type,
                description=task[:100],
                strategy=self._extract_strategy(request, success, result_preview, error),
                success_count=1 if success else 0,
                failure_count=0 if success else 1,
                consecutive_failures=1 if (not success and is_successive_failure) else 0,
                last_success=datetime.now().isoformat() if success else None,
                last_failure=datetime.now().isoformat() if not success else None,
                examples=[request[:200]],
            )
            self.patterns[pattern_id] = new_pattern
            actions = ["created"]

        self._save_patterns()
        result = ReflectionResult(
            trigger="task_complete",
            patterns_touched=[matched.pattern_id] if matched else [],
            actions=actions,
            summary=f"{'✅ 巩固' if success else '⚠️ 警告'} [{intent_type}]: {task[:50]}",
        )
        self._log_reflection(result)
        return result

    def _find_matching_pattern(self, intent_type: str, task: str) -> Optional[ExperiencePattern]:
        """找匹配的经验模式（模糊匹配intent_type + 任务关键词）"""
        task_lower = task.lower()
        for p in self.patterns.values():
            if not p.is_active:
                continue
            if p.intent_type == intent_type:
                # 同类型，看关键词重合度
                keywords = p.description.lower().split()
                overlap = sum(1 for kw in keywords if kw in task_lower)
                if overlap >= 1:
                    return p
        return None

    def _consolidate_pattern(self, pattern: ExperiencePattern, result_preview: str) -> list[str]:
        """巩固正确经验"""
        actions = []
        pattern.success_count += 1
        pattern.consecutive_failures = 0  # 重置连续失败计数
        pattern.last_success = datetime.now().isoformat()
        pattern.weight = min(pattern.weight * self.WEIGHT_BOOST_ON_SUCCESS, 2.0)

        # 更新examples（最多保留5个）
        if result_preview and len(pattern.examples) < 5:
            pattern.examples.append(result_preview[:100])

        actions.append("consolidated")
        logger.info(f"✅ 巩固模式 [{pattern.pattern_id}] weight={pattern.weight:.2f}")
        return actions

    def _weaken_pattern(self, pattern: ExperiencePattern, error: str) -> list[str]:
        """削弱/遗忘错误经验"""
        actions = []
        pattern.failure_count += 1
        pattern.consecutive_failures += 1
        pattern.last_failure = datetime.now().isoformat()
        pattern.weight *= self.WEIGHT_DECAY_ON_FAILURE

        if pattern.consecutive_failures >= self.CONSECUTIVE_FAILURE_THRESHOLD:
            # 连续失败超阈值 → 降权到最低或淘汰
            if pattern.weight < self.MIN_WEIGHT_THRESHOLD:
                pattern.is_active = False
                actions.append("forgotten")
                logger.warning(f"🗑️ 遗忘模式 [{pattern.pattern_id}]: 连续失败{pattern.consecutive_failures}次")
            else:
                actions.append("degraded")
                logger.warning(f"⚠️ 降权模式 [{pattern.pattern_id}] weight={pattern.weight:.2f}")
        else:
            actions.append("weakened")

        return actions

    def _extract_strategy(
        self, request: str, success: bool, result_preview: str, error: str
    ) -> str:
        """从任务中提取策略（给未来参考）"""
        if success:
            return f"成功完成: {request[:80]}"
        else:
            return f"失败: {request[:80]} | 错误: {error[:80]}"

    # ── 定时全量反思 ──────────────────────────────────────────────────────

    # ── 任务触发反思（供 evolution_memory 钩子调用） ──────────────────────

    def trigger_task_reflection(
        self,
        request: str = "",
        result: str = "",
        error: str = "",
        success: bool = True,
    ):
        """
        任务完成/失败后触发的小反思。

        由 evolution_memory.on_task_complete / on_task_failure 自动调用。
        在后台线程运行，不阻塞主流程。

        逻辑：
          成功 → 从 result 提取意图类型 → 巩固该模式
          失败 → 从 error 提取错误类型 → 警告该模式
        """
        import threading

        def _do_reflect():
            try:
                intent_type = self._infer_intent(request)
                task = request[:80] if request else "unknown"

                # 从 result/error 提取预览
                preview = (result or error)[:200]

                self.reflect_on_task(
                    success=success,
                    intent_type=intent_type,
                    task=task,
                    request=request,
                    result_preview=result[:200] if success else "",
                    error=error[:200] if not success else "",
                )
                logger.info(f"🧠 任务反思完成: {'✅' if success else '❌'} [{intent_type}]")
            except Exception as e:
                logger.warning(f"后台反思失败（静默）: {e}")

        thread = threading.Thread(target=_do_reflect, daemon=True)
        thread.start()

    def _infer_intent(self, request: str) -> str:
        """从请求中推断意图类型（简单关键词匹配）"""
        req_lower = request.lower()
        if any(k in req_lower for k in ["搜索", "查询", "了解", "查一下", "搜索"]):
            return "search"
        if any(k in req_lower for k in ["写代码", "开发", "实现", "写个", "帮我写"]):
            return "code_generation"
        if any(k in req_lower for k in ["git", "提交", "推送", "分支", "commit", "push"]):
            return "git_operation"
        if any(k in req_lower for k in ["飞书", "telegram", "通知", "消息"]):
            return "messaging"
        if any(k in req_lower for k in ["运行", "执行", "命令", "shell"]):
            return "shell_command"
        if any(k in req_lower for k in ["反思", "复盘", "总结"]):
            return "reflection"
        return "general"

    def get_best_strategy(self, intent_type: str, request: str = "") -> Optional[str]:
        """
        获取某意图类型的最佳策略描述。

        由 memory_injection 调用，用于注入到 LLM 上下文。
        """
        for p in self.patterns.values():
            if p.is_active and p.intent_type == intent_type and p.weight >= 0.5:
                return f"[最佳策略 {p.intent_type}] {p.strategy[:200]}"
        return None

    # ── 每日全量反思 ──────────────────────────────────────────────────────

    def daily_reflection(self, dry_run: bool = False) -> ReflectionResult:
        """
        每天全量反思：遍历所有模式，做整体优化。

        - 提升近期活跃的高权重模式
        - 淘汰长期不活跃的低权重模式
        - 合并相似的模式
        """
        now = datetime.now()
        actions = []
        touched = []

        for pattern_id, pattern in list(self.patterns.items()):
            if not pattern.is_active:
                continue

            touched.append(pattern_id)

            # 超过30天无更新且权重低 → 淘汰
            try:
                last_update_str = pattern.last_success or pattern.last_failure or pattern.created_at
                last_update = datetime.fromisoformat(last_update_str)
                days_inactive = (now - last_update).days

                if days_inactive >= 30 and pattern.weight < 0.3:
                    pattern.is_active = False
                    actions.append(f"forgotten:inactive:{pattern_id}")
                    logger.info(f"🗑️ 遗忘不活跃模式 [{pattern_id}]: {days_inactive}天无更新")
                    continue
            except Exception:
                pass

            # 长期成功且高权重 → 提炼为稳定策略（只提升weight上限）
            if pattern.success_count >= 5 and pattern.weight >= 1.5:
                pattern.weight = min(pattern.weight, 2.5)  # 最高2.5
                actions.append(f"stabilized:{pattern_id}")

        self._save_patterns()
        result = ReflectionResult(
            trigger="daily",
            patterns_touched=touched,
            actions=actions,
            summary=f"全量反思完成: 处理了 {len(touched)} 个模式，执行了 {len(actions)} 项操作",
        )
        self._log_reflection(result)
        logger.info(f"📋 每日反思完成: {result.summary}")
        return result

    # ── 经验查询 ──────────────────────────────────────────────────────────

    def get_best_strategy(self, intent_type: str, task_keywords: str = "") -> Optional[str]:
        """
        查询某类任务的最佳策略（给Agent参考）。

        找 weight 最高的活跃模式。
        """
        candidates = [
            p for p in self.patterns.values()
            if p.is_active and p.intent_type == intent_type
        ]
        if not candidates:
            return None

        # 按权重排序
        candidates.sort(key=lambda p: p.weight, reverse=True)
        best = candidates[0]

        # 构建策略提示
        lines = [
            f"[经验模式] {best.description}",
            f"策略: {best.strategy}",
            f"成功 {best.success_count} 次 | 失败 {best.failure_count} 次",
            f"置信度: {best.weight:.2f}",
        ]
        if best.examples:
            lines.append(f"案例: {best.examples[0][:100]}")
        return "\n".join(lines)

    def get_active_patterns(self) -> list[ExperiencePattern]:
        """获取所有活跃模式"""
        return [p for p in self.patterns.values() if p.is_active]

    def get_pattern_stats(self) -> dict:
        """获取模式统计"""
        all_patterns = list(self.patterns.values())
        active = [p for p in all_patterns if p.is_active]
        return {
            "total": len(all_patterns),
            "active": len(active),
            "forgotten": len(all_patterns) - len(active),
            "high_confidence": len([p for p in active if p.weight >= 1.5]),
            "avg_weight": sum(p.weight for p in active) / max(len(active), 1),
        }


# ── 全局单例 ────────────────────────────────────────────────────────────────

_reflection_engine: Optional[ReflectionEngine] = None


def get_reflection_engine() -> ReflectionEngine:
    global _reflection_engine
    if _reflection_engine is None:
        _reflection_engine = ReflectionEngine()
    return _reflection_engine
