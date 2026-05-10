"""
鸿钧 · 元学习引擎
================

从历史任务中学习：什么策略适合什么任务。

核心思想：
  - 不是教它"怎么做"，而是教它"用什么方法做"
  - 每次任务完成后，记录：任务特征 → 使用的策略 → 效果
  - 下次遇到类似任务，优先推荐成功率高的策略

策略类型：
  - planning_strategy: "sequential"（逐步）/ "parallel"（并行）/ "single_step"（一步到位）
  - execution_mode: "code_first"（先生成再验证）/ "plan_first"（先计划再执行）
  - verification_level: "basic"（基本验证）/ "cross_validate"（交叉验证）/ "strict"（严格）
  - retry_policy: "eager"（失败立即重试）/ "cautious"（分析后再重试）/ "never"（不重试）

使用方式：
    ml = MetaLearner()
    strategy = ml.recommend(task_request="帮我开发一个矩阵动画")
    print(strategy)  # {"planning": "single_step", "execution": "code_first", "verification": "strict", "retry": "eager"}
"""

from __future__ import annotations
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.meta_learner")

META_DIR = Path.home() / ".hongjun"
META_FILE = META_DIR / "strategy_preferences.json"


# ── 策略定义 ────────────────────────────────────────────────────────────────

PLANNING_STRATEGIES = ["sequential", "parallel", "single_step"]
EXECUTION_MODES = ["code_first", "plan_first"]
VERIFICATION_LEVELS = ["basic", "cross_validate", "strict"]
RETRY_POLICIES = ["eager", "cautious", "never"]


class StrategyRecommendation:
    """策略推荐结果"""

    def __init__(
        self,
        planning_strategy: str = "sequential",
        execution_mode: str = "plan_first",
        verification_level: str = "basic",
        retry_policy: str = "cautious",
        confidence: float = 0.0,
        learned_from: int = 0,
        reason: str = "",
    ):
        self.planning_strategy = planning_strategy
        self.execution_mode = execution_mode
        self.verification_level = verification_level
        self.retry_policy = retry_policy
        self.confidence = confidence
        self.learned_from = learned_from
        self.reason = reason

    def to_dict(self) -> dict:
        return {
            "planning_strategy": self.planning_strategy,
            "execution_mode": self.execution_mode,
            "verification_level": self.verification_level,
            "retry_policy": self.retry_policy,
            "confidence": round(self.confidence, 3),
            "learned_from": self.learned_from,
            "reason": self.reason,
        }

    def __repr__(self) -> str:
        return (
            f"Strategy(plan={self.planning_strategy}, exec={self.execution_mode}, "
            f"verify={self.verification_level}, retry={self.retry_policy}, "
            f"conf={self.confidence:.0%})"
        )


# ── 任务特征提取 ────────────────────────────────────────────────────────────

class TaskFeatureExtractor:
    """从任务请求中提取特征"""

    FEATURE_PATTERNS = {
        "is_visual": [
            r"动画", r"游戏", r"可视化", r"canvas", r"webgl", r"粒子",
            r"图形", r"界面", r"UI", r"图表", r"3D", r"three\.js",
            r"matrix", r"雪花", r"雨", r"星空",
        ],
        "is_code_generation": [
            r"写代码", r"开发", r"实现", r"生成\w+代码", r"编写",
            r"function", r"def\s+\w+", r"class\s+\w+",
        ],
        "is_search": [
            r"搜索", r"查询", r"了解", r"研究", r"调研",
            r"找一下", r"有什么", r"帮我查",
        ],
        "is_messaging": [
            r"发送", r"通知", r"推送", r"发消息", r"飞书", r"telegram",
        ],
        "is_multi_step": [
            r"首先", r"然后", r"接下来", r"最后", r"一步步", r"步骤",
            r"分三步", r"分阶段",
        ],
        "is_long_running": [
            r"持续", r"定时", r"定期", r"监控", r"cron", r"每天",
            r"每小时", r"后台运行",
        ],
        "is_file_operation": [
            r"读取", r"写入", r"保存", r"上传", r"下载", r"文件",
        ],
        "is_browser": [
            r"浏览器", r"网页", r"打开", r"访问", r"网站", r"html",
        ],
        "is_data_processing": [
            r"分析", r"处理", r"统计", r"批量", r"转换", r"导出",
            r"excel", r"csv", r"json",
        ],
        "is_critical": [
            r"紧急", r"重要", r"必须", r"立即", r"马上",
        ],
    }

    @classmethod
    def extract(cls, request: str) -> dict[str, bool]:
        """从请求中提取特征"""
        request_lower = request.lower()
        features = {}
        for feat_name, patterns in cls.FEATURE_PATTERNS.items():
            features[feat_name] = any(
                re.search(pat, request_lower) for pat in patterns
            )
        return features


# ── 策略效果记录 ────────────────────────────────────────────────────────────

class StrategyOutcome:
    """一次策略执行结果"""

    def __init__(
        self,
        task_request: str,
        intent: str,
        features: dict[str, bool],
        strategy: dict,
        success: bool,
        error: str = "",
        execution_time: float = 0,
        attempts: int = 1,
        timestamp: str = "",
    ):
        self.task_request = task_request
        self.intent = intent
        self.features = features
        self.strategy = strategy
        self.success = success
        self.error = error
        self.execution_time = execution_time
        self.attempts = attempts
        self.timestamp = timestamp or datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "task_request": self.task_request[:200],
            "intent": self.intent,
            "features": self.features,
            "strategy": self.strategy,
            "success": self.success,
            "error": self.error[:200] if self.error else "",
            "execution_time": round(self.execution_time, 2),
            "attempts": self.attempts,
            "timestamp": self.timestamp,
        }


# ── 元学习器 ────────────────────────────────────────────────────────────────

class MetaLearner:
    """
    元学习器：从历史任务中学习策略选择。

    使用方式：
        ml = MetaLearner()

        # 收到新任务 → 获取推荐策略
        strategy = ml.recommend("帮我开发一个飞书通知功能")
        print(f"推荐策略: {strategy}")

        # 任务执行完成后 → 记录结果
        ml.record(
            task_request="帮我开发一个飞书通知功能",
            intent="messaging",
            strategy=strategy.to_dict(),
            success=True,
            execution_time=12.5,
        )
    """

    def __init__(self):
        META_DIR.mkdir(parents=True, exist_ok=True)
        self.strategy_history: list[StrategyOutcome] = []
        self._strategy_scores: dict[str, dict[str, dict]] = defaultdict(
            lambda: defaultdict(lambda: {"wins": 0, "losses": 0, "total_time": 0.0, "count": 0})
        )
        self._load()

    # ── 持久化 ──────────────────────────────────────────────────────────

    def _load(self):
        """加载历史记录"""
        if not META_FILE.exists():
            return
        try:
            with open(META_DIR / "strategy_preferences.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                self._strategy_scores = defaultdict(
                    lambda: defaultdict(lambda: {"wins": 0, "losses": 0, "total_time": 0.0, "count": 0})
                )
                for feat_key, strategies in data.get("scores", {}).items():
                    for strat_key, scores in strategies.items():
                        self._strategy_scores[feat_key][strat_key] = scores
                logger.info(f"元学习器加载了 {len(self._strategy_scores)} 个特征维度的策略数据")
        except Exception as e:
            logger.warning(f"加载元学习数据失败: {e}")

    def _save(self):
        """保存历史记录"""
        try:
            data = {
                "version": 1,
                "updated_at": datetime.now().isoformat(),
                "scores": dict(self._strategy_scores),
            }
            with open(META_DIR / "strategy_preferences.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存元学习数据失败: {e}")

    # ── 推荐 ────────────────────────────────────────────────────────────

    def recommend(self, task_request: str, intent: str = "") -> StrategyRecommendation:
        """
        根据任务特征推荐最佳策略。

        Args:
            task_request: 用户请求原文
            intent: 意图分类（如有）

        Returns:
            StrategyRecommendation，包含4个维度的推荐
        """
        features = TaskFeatureExtractor.extract(task_request)

        # 各维度决策
        planning = self._decide_planning(features, task_request)
        execution = self._decide_execution(features, task_request)
        verification = self._decide_verification(features, task_request)
        retry = self._decide_retry(features, task_request, execution)

        # 计算置信度（基于历史样本数）
        min_samples = min(
            self._strategy_scores["planning"].get(planning, {}).get("count", 0),
            self._strategy_scores["execution"].get(execution, {}).get("count", 0),
            self._strategy_scores["verification"].get(verification, {}).get("count", 0),
            self._strategy_scores["retry"].get(retry, {}).get("count", 0),
        )
        confidence = min(min_samples / 10.0, 0.9)  # 最多10个样本达到90%置信度

        reason = self._build_reason(features, planning, execution, verification, retry)

        return StrategyRecommendation(
            planning_strategy=planning,
            execution_mode=execution,
            verification_level=verification,
            retry_policy=retry,
            confidence=confidence,
            learned_from=min_samples,
            reason=reason,
        )

    def _decide_planning(self, features: dict, request: str) -> str:
        """决定规划策略"""
        # 有历史数据 → 用学习到的
        best = self._best_option("planning", features, request)
        if best:
            return best

        # 无数据 → 规则推理
        if features["is_multi_step"] or features["is_long_running"]:
            return "sequential"
        if features["is_visual"] or features["is_code_generation"]:
            return "single_step"
        return "sequential"

    def _decide_execution(self, features: dict, request: str) -> str:
        """决定执行模式"""
        best = self._best_option("execution", features, request)
        if best:
            return best

        if features["is_code_generation"] or features["is_visual"]:
            return "code_first"
        return "plan_first"

    def _decide_verification(self, features: dict, request: str) -> str:
        """决定验证级别"""
        best = self._best_option("verification", features, request)
        if best:
            return best

        if features["is_critical"] or features["is_visual"]:
            return "strict"
        if features["is_multi_step"]:
            return "cross_validate"
        return "basic"

    def _decide_retry(self, features: dict, request: str, execution: str) -> str:
        """决定重试策略"""
        best = self._best_option("retry", features, request)
        if best:
            return best

        if execution == "code_first":
            return "eager"
        return "cautious"

    def _best_option(self, dimension: str, features: dict, request: str) -> Optional[str]:
        """从历史数据中找到最佳选项"""
        if dimension not in self._strategy_scores:
            return None

        candidates = list(self._strategy_scores[dimension].keys())
        if not candidates:
            return None

        scores = {}
        for candidate in candidates:
            s = self._strategy_scores[dimension][candidate]
            total = s["wins"] + s["losses"]
            if total == 0:
                scores[candidate] = 0.0
            else:
                win_rate = s["wins"] / total
                # 考虑速度：越快越好
                avg_time = s["total_time"] / s["count"] if s["count"] > 0 else 999
                scores[candidate] = win_rate * 0.7 + max(0, 1 - avg_time / 60) * 0.3

        return max(scores, key=scores.get)

    def _build_reason(
        self, features: dict, planning: str, execution: str, verification: str, retry: str
    ) -> str:
        """生成推荐理由"""
        parts = []
        if features["is_visual"]:
            parts.append("视觉任务→单步执行+严格验证")
        elif features["is_multi_step"]:
            parts.append("多步骤→顺序规划")
        elif features["is_code_generation"]:
            parts.append("代码生成→先生成")
        if features["is_critical"]:
            parts.append("关键任务→严格验证")
        return "; ".join(parts) if parts else "基于任务特征推荐"

    # ── 记录 ───────────────────────────────────────────────────────────

    def record(
        self,
        task_request: str,
        intent: str,
        strategy: dict,
        success: bool,
        error: str = "",
        execution_time: float = 0,
        attempts: int = 1,
    ):
        """
        记录一次策略执行结果，用于学习。

        Args:
            task_request: 用户原始请求
            intent: 意图分类
            strategy: 使用的策略 dict
            success: 是否成功
            error: 错误信息（如失败）
            execution_time: 执行耗时（秒）
            attempts: 重试次数
        """
        features = TaskFeatureExtractor.extract(task_request)
        outcome = StrategyOutcome(
            task_request=task_request,
            intent=intent,
            features=features,
            strategy=strategy,
            success=success,
            error=error,
            execution_time=execution_time,
            attempts=attempts,
        )
        self.strategy_history.insert(0, outcome)
        self.strategy_history = self.strategy_history[:500]  # 最多500条

        # 更新各维度得分
        self._update_scores(strategy, success, execution_time)

        self._save()
        logger.info(
            f"元学习记录: plan={strategy.get('planning_strategy')} "
            f"→ {'✅' if success else '❌'} (conf={self._compute_confidence():.0%})"
        )

    def _update_scores(self, strategy: dict, success: bool, execution_time: float):
        """更新策略得分"""
        for dimension, key in [
            ("planning", strategy.get("planning_strategy", "sequential")),
            ("execution", strategy.get("execution_mode", "plan_first")),
            ("verification", strategy.get("verification_level", "basic")),
            ("retry", strategy.get("retry_policy", "cautious")),
        ]:
            scores = self._strategy_scores[dimension][key]
            if success:
                scores["wins"] += 1
            else:
                scores["losses"] += 1
            scores["total_time"] += execution_time
            scores["count"] += 1

    def _compute_confidence(self) -> float:
        """计算整体置信度"""
        total = sum(
            s["count"]
            for dim in self._strategy_scores.values()
            for s in dim.values()
        )
        return min(total / 20.0, 0.9)

    # ── 分析 ───────────────────────────────────────────────────────────

    def get_best_for_intent(self, intent: str) -> Optional[StrategyRecommendation]:
        """获取某个意图的最佳策略"""
        outcomes = [o for o in self.strategy_history if o.intent == intent]
        if not outcomes:
            return None

        # 按意图+策略分组找最优
        by_strat: dict[str, list[StrategyOutcome]] = defaultdict(list)
        for o in outcomes:
            key = json.dumps(o.strategy, sort_keys=True)
            by_strat[key].append(o)

        best_key = max(
            by_strat,
            key=lambda k: sum(1 for o in by_strat[k] if o.success) / len(by_strat[k]),
        )
        best_outcomes = by_strat[best_key]
        success_rate = sum(1 for o in best_outcomes if o.success) / len(best_outcomes)

        strat_dict = json.loads(best_key)
        return StrategyRecommendation(
            planning_strategy=strat_dict.get("planning_strategy", "sequential"),
            execution_mode=strat_dict.get("execution_mode", "plan_first"),
            verification_level=strat_dict.get("verification_level", "basic"),
            retry_policy=strat_dict.get("retry_policy", "cautious"),
            confidence=min(len(best_outcomes) / 5.0, 0.9),
            learned_from=len(best_outcomes),
            reason=f"基于 {len(best_outcomes)} 次 '{intent}' 任务学习",
        )

    def get_stats(self) -> dict:
        """获取元学习统计"""
        total = sum(s["count"] for dim in self._strategy_scores.values() for s in dim.values())
        wins = sum(s["wins"] for dim in self._strategy_scores.values() for s in dim.values())
        losses = sum(s["losses"] for dim in self._strategy_scores.values() for s in dim.values())

        # 各维度最优策略
        best_strategies = {}
        for dim in ["planning", "execution", "verification", "retry"]:
            best = self._best_option(dim, {}, "")
            if best:
                s = self._strategy_scores[dim][best]
                win_rate = s["wins"] / (s["wins"] + s["losses"]) if (s["wins"] + s["losses"]) > 0 else 0
                best_strategies[dim] = {"strategy": best, "win_rate": win_rate, "samples": s["count"]}

        return {
            "total_recorded": total,
            "wins": wins,
            "losses": losses,
            "overall_win_rate": round(wins / (wins + losses), 3) if (wins + losses) > 0 else 0,
            "best_strategies": best_strategies,
            "history_size": len(self.strategy_history),
        }


# ── 全局单例 ────────────────────────────────────────────────────────────────

_learner: Optional[MetaLearner] = None


def get_learner() -> MetaLearner:
    global _learner
    if _learner is None:
        _learner = MetaLearner()
    return _learner
