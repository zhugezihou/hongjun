"""
刑部 · 质量评估
================

鸿钧 Agent 的质量保障层。

每次任务执行后自动评估：
  1. 正确性：结果是否符合任务要求
  2. 完整性：是否覆盖了所有子任务
  3. 安全性：是否有数据泄露风险
  4. 性能：执行时间是否合理

用法：
  evaluator = HongjunEvaluator()
  report = evaluator.evaluate(
      task="搜索 GitHub 趋势",
      result="返回了 5 个项目列表",
      execution_time_ms=1500,
  )
  print(report["score"], report["grade"])
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime


@dataclass
class EvaluationReport:
    """评估报告"""
    task: str
    result: str
    overall_score: float  # 0.0 - 1.0
    grade: str            # A/B/C/D/F
    dimensions: Dict[str, float]
    warnings: List[str]
    suggestions: List[str]
    evaluated_at: str


class HongjunEvaluator:
    """
    鸿钧质量评估器（刑部尚书）

    评估维度：
      correctness  — 正确性
      completeness — 完整性
      security     — 安全性
      performance  — 性能
      clarity      — 清晰度
    """

    def __init__(self):
        self.evaluation_history: List[EvaluationReport] = []

    def evaluate(
        self,
        task: str,
        result: str,
        execution_time_ms: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> EvaluationReport:
        """
        评估任务执行质量

        Args:
            task: 原始任务描述
            result: 执行结果
            execution_time_ms: 执行耗时（毫秒）
            context: 额外上下文

        Returns:
            EvaluationReport
        """
        scores = {}

        # 1. 正确性评分
        scores["correctness"] = self._eval_correctness(task, result)

        # 2. 完整性评分
        scores["completeness"] = self._eval_completeness(task, result)

        # 3. 安全性评分
        scores["security"] = self._eval_security(result)

        # 4. 性能评分
        if execution_time_ms is not None:
            scores["performance"] = self._eval_performance(execution_time_ms)
        else:
            scores["performance"] = 1.0

        # 5. 清晰度评分
        scores["clarity"] = self._eval_clarity(result)

        # 综合评分
        overall = sum(scores.values()) / len(scores)
        grade = self._score_to_grade(overall)

        # 生成警告和建议
        warnings = self._generate_warnings(task, result, scores)
        suggestions = self._generate_suggestions(scores)

        report = EvaluationReport(
            task=task,
            result=result,
            overall_score=round(overall, 3),
            grade=grade,
            dimensions={k: round(v, 3) for k, v in scores.items()},
            warnings=warnings,
            suggestions=suggestions,
            evaluated_at=datetime.now().isoformat(),
        )

        self.evaluation_history.append(report)
        return report

    def _eval_correctness(self, task: str, result: str) -> float:
        """评估正确性：结果是否正确回答了任务"""
        if not result or result.startswith("❌"):
            return 0.0

        task_lower = task.lower()
        result_lower = result.lower()

        # 关键词匹配
        if any(kw in result_lower for kw in ["error", "failed", "失败"]):
            if "错误" not in task:
                return 0.3

        # 基本完整性检查
        if len(result) < 10:
            return 0.2

        # 如果任务包含特定要求，检查是否满足
        if "搜索" in task and len(result) > 50:
            return 0.85

        if "代码" in task and "```" in result:
            return 0.9

        return 0.75  # 默认良好

    def _eval_completeness(self, task: str, result: str) -> float:
        """评估完整性：是否覆盖了所有子任务"""
        score = 1.0

        # 检查结果长度是否合理
        if len(result) < 50:
            score -= 0.3

        # 检查是否有截断标记
        if "..." in result or "（已截断）" in result:
            score -= 0.1

        return max(0.0, score)

    def _eval_security(self, result: str) -> float:
        """评估安全性：是否有数据泄露风险"""
        # 敏感信息检测
        sensitive_patterns = [
            r"\b\d{16}\b",  # 信用卡
            r"sk-[a-zA-Z0-9]{20,}",  # OpenAI key
            r"api[_-]?key['\"]?\s*[:=]\s*['\"]?\w+",  # API key
            r"password\s*[:=]\s*['\"]?\S+",  # Password
        ]

        import re
        for pattern in sensitive_patterns:
            if re.search(pattern, result, re.IGNORECASE):
                return 0.3  # 检测到敏感信息，降低评分

        return 1.0

    def _eval_performance(self, execution_time_ms: float) -> float:
        """评估性能：执行时间是否合理"""
        if execution_time_ms < 1000:
            return 1.0  # < 1s，优秀
        elif execution_time_ms < 5000:
            return 0.9  # < 5s，良好
        elif execution_time_ms < 30000:
            return 0.7  # < 30s，正常
        elif execution_time_ms < 60000:
            return 0.4  # < 60s，较慢
        else:
            return 0.2  # > 60s，过慢

    def _eval_clarity(self, result: str) -> float:
        """评估清晰度：结果表达是否清晰"""
        score = 1.0

        # 检查是否太短
        if len(result) < 30:
            score -= 0.2

        # 检查是否有 Markdown 格式
        if "```" in result or "**" in result or "•" in result:
            score += 0.05  # 有格式，略微加分

        # 检查是否有乱码/乱字符
        if "�" in result or "\x00" in result:
            score -= 0.3

        return max(0.0, min(1.0, score))

    def _score_to_grade(self, score: float) -> str:
        """分数转等级"""
        if score >= 0.9:
            return "A"
        elif score >= 0.8:
            return "B"
        elif score >= 0.7:
            return "C"
        elif score >= 0.5:
            return "D"
        else:
            return "F"

    def _generate_warnings(
        self,
        task: str,
        result: str,
        scores: Dict[str, float],
    ) -> List[str]:
        """生成警告"""
        warnings = []

        if scores.get("correctness", 1.0) < 0.5:
            warnings.append("⚠️ 正确性存疑，建议复核结果")

        if scores.get("security", 1.0) < 0.5:
            warnings.append("🔒 检测到潜在敏感信息泄露风险")

        if scores.get("performance", 1.0) < 0.5:
            warnings.append("⏱️ 性能较慢，可考虑优化")

        if not result or len(result.strip()) == 0:
            warnings.append("❓ 结果为空，建议补充")

        return warnings

    def _generate_suggestions(self, scores: Dict[str, float]) -> List[str]:
        """生成改进建议"""
        suggestions = []

        if scores.get("clarity", 1.0) < 0.8:
            suggestions.append("💡 建议使用 Markdown 格式提升可读性")

        if scores.get("completeness", 1.0) < 0.8:
            suggestions.append("💡 建议补充更多细节信息")

        return suggestions

    def get_summary(self) -> Dict[str, Any]:
        """获取评估历史摘要"""
        if not self.evaluation_history:
            return {"total": 0, "avg_score": 0.0}

        total = len(self.evaluation_history)
        avg_score = sum(r.overall_score for r in self.evaluation_history) / total
        avg_by_dim = {}
        for dim in ["correctness", "completeness", "security", "performance", "clarity"]:
            dim_scores = [r.dimensions.get(dim, 0) for r in self.evaluation_history]
            avg_by_dim[dim] = round(sum(dim_scores) / len(dim_scores), 3)

        return {
            "total": total,
            "avg_score": round(avg_score, 3),
            "avg_by_dimension": avg_by_dim,
            "grade_distribution": {
                "A": sum(1 for r in self.evaluation_history if r.grade == "A"),
                "B": sum(1 for r in self.evaluation_history if r.grade == "B"),
                "C": sum(1 for r in self.evaluation_history if r.grade == "C"),
                "D": sum(1 for r in self.evaluation_history if r.grade == "D"),
                "F": sum(1 for r in self.evaluation_history if r.grade == "F"),
            },
        }

    def format_report(self, report: EvaluationReport) -> str:
        """格式化评估报告为可读字符串"""
        lines = [
            "📊 刑部·质量评估报告",
            f"{'=' * 40}",
            f"综合评分: {report.overall_score:.1%}  等级: {report.grade}",
            "",
            "分项得分:",
        ]

        for dim, score in report.dimensions.items():
            bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
            lines.append(f"  {dim:15s} [{bar}] {score:.0%}")

        if report.warnings:
            lines.append("")
            lines.append("⚠️  警告:")
            for w in report.warnings:
                lines.append(f"  {w}")

        if report.suggestions:
            lines.append("")
            lines.append("💡 建议:")
            for s in report.suggestions:
                lines.append(f"  {s}")

        lines.append(f"\n评估时间: {report.evaluated_at}")

        return "\n".join(lines)


# === 单元测试 ===
if __name__ == "__main__":
    evaluator = HongjunEvaluator()

    test_cases = [
        (
            "搜索 GitHub 今天的 AI Agent 趋势",
            "🔍 GitHub Trending Top3:\n1. browser-use (91k★)\n2. LangGraph (65k★)\n3. MemPalace (17k★)",
            1500,
        ),
        (
            "写一个快速排序",
            "```python\ndef quicksort(arr):\n    if len(arr) <= 1: return arr\n    pivot = arr[len(arr)//2]\n    return quicksort([x for x in arr if x < pivot]) + quicksort([x for x in arr if x >= pivot])\n```",
            800,
        ),
    ]

    for task, result, ms in test_cases:
        report = evaluator.evaluate(task, result, ms)
        print(evaluator.format_report(report))
        print()
