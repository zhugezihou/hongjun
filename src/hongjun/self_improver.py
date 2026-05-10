"""
鸿钧 · 自我改进引擎
==================

基于反思结果和错误模式，主动改进自身代码质量。

核心能力：
  1. 分析反思结果 → 识别代码改进点（不是bug，而是设计/性能/可维护性）
  2. 生成改进方案 → LLM 参与评估（但不每次都改，保守进行）
  3. 应用前验证 → 用 self_check.py 确认系统仍健康
  4. 记录改进历史 → 每次改进都记录，下次同类改进参考

改进类型：
  - code_quality: 代码重复、过长函数、缺少注释
  - performance: 重复计算、不必要的循环、内存泄漏
  - robustness: 缺少异常处理、边界条件未覆盖
  - architecture: 模块耦合过紧、职责不清

安全边界：
  - 仅修改 SAFE_TO_MODIFY 中的模块
  - 每次修改前执行健康检查
  - 任何修改都必须 git commit 可回滚
  - 被 self_repair 标记为"受保护"的模块永不修改

使用方式：
    improver = SelfImprover()
    suggestions = improver.analyze()
    if suggestions:
        for s in suggestions:
            print(f"[{s.priority}] {s.module}: {s.description}")
            print(f"  当前代码: {s.current_snippet[:100]}")
            print(f"  建议改进: {s.suggested_fix[:100]}")
    # 选择性应用
    result = improver.apply(suggestions[0])
"""

from __future__ import annotations
import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from hongjun.logging_config import get_logger
from hongjun.self_repair import SAFE_TO_MODIFY

logger = get_logger("hongjun.self_improver")

IMPROVEMENTS_DIR = Path.home() / ".hongjun"
IMPROVEMENTS_FILE = IMPROVEMENTS_DIR / "improvements.json"


# ── 改进建议 ────────────────────────────────────────────────────────────────

@dataclass
class ImprovementSuggestion:
    """改进建议"""
    module: str
    improvement_type: str  # code_quality / performance / robustness / architecture
    priority: int  # 1=高 2=中 3=低
    description: str
    current_snippet: str
    suggested_fix: str
    confidence: float  # 0-1，置信度
    effort: str  # "small" / "medium" / "large"
    based_on: str  # 来源：reflection / error_pattern / manual


@dataclass
class ImprovementResult:
    """改进结果"""
    suggestion: ImprovementSuggestion
    applied: bool
    commit_sha: str = ""
    verification_passed: bool = False
    error: str = ""
    timestamp: str = ""


# ── 自我改进器 ──────────────────────────────────────────────────────────────

class SelfImprover:
    """
    自我改进引擎。

    使用方式：
        improver = SelfImprover()
        suggestions = improver.analyze()
        result = improver.apply(suggestions[0])
    """

    def __init__(self, core_dir: Optional[Path] = None):
        self.core_dir = core_dir or Path(__file__).parent.parent / "hongjun"
        self._improvements: list[ImprovementResult] = []
        self._load_improvements()

    def _load_improvements(self):
        """加载历史改进记录"""
        if not IMPROVEMENTS_FILE.exists():
            return
        try:
            with open(IMPROVEMENTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._improvements = [
                    ImprovementResult(
                        suggestion=ImprovementSuggestion(**item["suggestion"]),
                        applied=item["applied"],
                        commit_sha=item.get("commit_sha", ""),
                        verification_passed=item.get("verification_passed", False),
                        timestamp=item.get("timestamp", ""),
                    )
                    for item in data.get("improvements", [])
                ]
            logger.info(f"自我改进器加载了 {len(self._improvements)} 条历史记录")
        except Exception as e:
            logger.warning(f"加载改进历史失败: {e}")

    def _save_improvements(self):
        """保存改进历史"""
        try:
            IMPROVEMENTS_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "updated_at": datetime.now().isoformat(),
                "improvements": [
                    {
                        "suggestion": {
                            "module": r.suggestion.module,
                            "improvement_type": r.suggestion.improvement_type,
                            "priority": r.suggestion.priority,
                            "description": r.suggestion.description,
                            "current_snippet": r.suggestion.current_snippet,
                            "suggested_fix": r.suggestion.suggested_fix,
                            "confidence": r.suggestion.confidence,
                            "effort": r.suggestion.effort,
                            "based_on": r.suggestion.based_on,
                        },
                        "applied": r.applied,
                        "commit_sha": r.commit_sha,
                        "verification_passed": r.verification_passed,
                        "timestamp": r.timestamp,
                    }
                    for r in self._improvements
                ],
            }
            with open(IMPROVEMENTS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存改进历史失败: {e}")

    # ── 分析 ───────────────────────────────────────────────────────────

    def analyze(self) -> list[ImprovementSuggestion]:
        """
        分析所有模块，生成改进建议。

        Returns:
            改进建议列表（按优先级排序）
        """
        suggestions: list[ImprovementSuggestion] = []

        # 1. 分析错误模式 → 识别高频问题模块
        suggestions.extend(self._analyze_error_patterns())

        # 2. 分析反思引擎 → 识别反复失败的策略
        suggestions.extend(self._analyze_reflection())

        # 3. 分析代码质量 → 重复、过长、缺少处理
        suggestions.extend(self._analyze_code_quality())

        # 4. 去重：相同模块相同类型只保留最高优先级
        suggestions = self._deduplicate(suggestions)

        # 5. 过滤：已经最近改过的模块（90天内）不重复改
        suggestions = self._filter_recent(suggestions)

        # 按优先级排序
        suggestions.sort(key=lambda s: (s.priority, -s.confidence))
        return suggestions

    def _analyze_error_patterns(self) -> list[ImprovementSuggestion]:
        """基于错误模式分析 → 识别需要改进的模块"""
        suggestions = []
        try:
            from hongjun.error_pattern import get_error_library

            lib = get_error_library()
            stats = lib.get_stats()

            # 找出高频失败的模块
            for p in stats.get("top_patterns", []):
                if p["times_seen"] >= 3 and p["success_rate"] < 0.6:
                    # 该模块反复失败且成功率低 → 建议改进
                    module = p.get("module", "unknown")
                    if module in SAFE_TO_MODIFY:
                        suggestions.append(ImprovementSuggestion(
                            module=module,
                            improvement_type="robustness",
                            priority=1,
                            description=f"模块 '{module}' 错误 '{p['error_type']}' 出现 {p['times_seen']} 次，成功率仅 {p['success_rate']:.0%}",
                            current_snippet=f"错误类型: {p['error_type']}, 出现次数: {p['times_seen']}",
                            suggested_fix="建议增加该模块的错误处理和边界条件检查",
                            confidence=0.8,
                            effort="medium",
                            based_on="error_pattern",
                        ))
        except Exception as e:
            logger.warning(f"分析错误模式失败: {e}")

        return suggestions

    def _analyze_reflection(self) -> list[ImprovementSuggestion]:
        """基于反思结果 → 识别需要改进的策略"""
        suggestions = []
        try:
            from hongjun.evolution_memory import EvolutionMemory

            mem = EvolutionMemory()

            # 找出反复失败的模块
            failures = mem.data.get("failures", [])
            module_failures: dict[str, int] = {}
            for f in failures:
                for mod in f.get("modules_involved", []):
                    if mod in SAFE_TO_MODIFY:
                        module_failures[mod] = module_failures.get(mod, 0) + 1

            for mod, count in module_failures.items():
                if count >= 3:
                    recent = [f for f in failures if mod in f.get("modules_involved", [])][:3]
                    error_sample = recent[0].get("error", "")[:150] if recent else ""
                    suggestions.append(ImprovementSuggestion(
                        module=mod,
                        improvement_type="robustness",
                        priority=2,
                        description=f"模块 '{mod}' 在反思记录中出现 {count} 次失败",
                        current_snippet=error_sample,
                        suggested_fix="建议增加防御性编程，检查输入参数，添加超时控制",
                        confidence=0.6,
                        effort="medium",
                        based_on="reflection",
                    ))
        except Exception as e:
            logger.warning(f"分析反思结果失败: {e}")

        return suggestions

    def _analyze_code_quality(self) -> list[ImprovementSuggestion]:
        """分析代码质量 → 识别具体改进点"""
        suggestions = []
        try:
            for module_name in SAFE_TO_MODIFY:
                mod_path = self.core_dir / f"{module_name}.py"
                if not mod_path.exists():
                    continue

                code = mod_path.read_text(encoding="utf-8")
                lines = code.split("\n")

                # 检测1：超长文件（>2000行）
                if len(lines) > 2000:
                    suggestions.append(ImprovementSuggestion(
                        module=module_name,
                        improvement_type="architecture",
                        priority=2,
                        description=f"模块 {module_name}.py 有 {len(lines)} 行，建议拆分为更小的模块",
                        current_snippet=f"# 共 {len(lines)} 行",
                        suggested_fix="按功能拆分为多个子模块",
                        confidence=0.7,
                        effort="large",
                        based_on="static_analysis",
                    ))

                # 检测2：缺少 docstring 的顶层函数
                for i, line in enumerate(lines):
                    if line.strip().startswith("def ") and i > 0:
                        # 检查前面是否有 docstring
                        prev_doc = any('"""' in line for line in lines[max(0, i - 5):i])
                        if not prev_doc and len(lines) > 50:
                            fn_name = line.strip().split("(")[0].replace("def ", "")
                            suggestions.append(ImprovementSuggestion(
                                module=module_name,
                                improvement_type="code_quality",
                                priority=3,
                                description=f"函数 '{fn_name}' 缺少文档字符串",
                                current_snippet=line.strip(),
                                suggested_fix=f"添加 def {fn_name}(...): 的 docstring",
                                confidence=0.5,
                                effort="small",
                                based_on="static_analysis",
                            ))

        except Exception as e:
            logger.warning(f"分析代码质量失败: {e}")

        return suggestions

    def _deduplicate(
        self, suggestions: list[ImprovementSuggestion]
    ) -> list[ImprovementSuggestion]:
        """去重：同模块同类型保留最高优先级"""
        seen: dict[tuple, ImprovementSuggestion] = {}
        for s in suggestions:
            key = (s.module, s.improvement_type)
            if key not in seen or s.priority < seen[key].priority:
                seen[key] = s
        return list(seen.values())

    def _filter_recent(
        self, suggestions: list[ImprovementSuggestion]
    ) -> list[ImprovementSuggestion]:
        """过滤90天内已改进过的模块"""
        cutoff = datetime.now() - timedelta(days=90)
        recent_modules = {
            r.suggestion.module
            for r in self._improvements
            if r.applied and r.timestamp and datetime.fromisoformat(r.timestamp) > cutoff
        }
        return [s for s in suggestions if s.module not in recent_modules]

    # ── 应用 ───────────────────────────────────────────────────────────

    def apply(self, suggestion: ImprovementSuggestion) -> ImprovementResult:
        """
        应用一条改进建议。

        流程：
          1. 检查模块是否在 SAFE_TO_MODIFY
          2. 获取当前代码
          3. 用 LLM 生成改进代码
          4. 应用前健康检查
          5. 应用改进
          6. git commit
          7. 验证（再次健康检查）
          8. 记录结果
        """
        result = ImprovementResult(
            suggestion=suggestion,
            applied=False,
            timestamp=datetime.now().isoformat(),
        )

        # 安全检查
        if suggestion.module not in SAFE_TO_MODIFY:
            result.error = f"模块 {suggestion.module} 不在可修改白名单"
            logger.warning(result.error)
            return result

        mod_path = self.core_dir / f"{suggestion.module}.py"
        if not mod_path.exists():
            result.error = f"模块文件不存在: {mod_path}"
            logger.warning(result.error)
            return result

        # 预检查：确保系统当前健康
        if not self._health_check():
            result.error = "系统健康检查未通过，暂不进行改进"
            return result

        # 生成改进代码（用 LLM）
        improved_code = self._generate_improvement(mod_path, suggestion)
        if improved_code is None:
            result.error = "LLM 未能生成有效的改进代码"
            return result

        # 应用前 git commit 当前状态（便于回滚）
        commit_sha = self._git_commit_prestate(mod_path)
        if not commit_sha:
            result.error = "git commit 当前状态失败，跳过改进"
            return result
        result.commit_sha = commit_sha

        # 应用改进
        try:
            old_content = mod_path.read_text(encoding="utf-8")
            mod_path.write_text(improved_code, encoding="utf-8")
            logger.info(f"应用改进 [{suggestion.module}]: {suggestion.description}")
        except Exception as e:
            mod_path.write_text(old_content, encoding="utf-8")  # 回滚
            result.error = f"写入失败，已回滚: {e}"
            return result

        # 验证
        if self._health_check():
            result.verification_passed = True
            result.applied = True

            # 正式 git commit
            self._git_commit_improvement(mod_path, suggestion)

            logger.info(f"✅ 改进 [{suggestion.module}] 成功: {suggestion.description}")
        else:
            # 健康检查失败，回滚
            mod_path.write_text(old_content, encoding="utf-8")
            result.error = "健康检查失败，已回滚改进"
            result.verification_passed = False
            logger.warning(f"❌ 改进 [{suggestion.module}] 健康检查失败，已回滚")

        self._improvements.insert(0, result)
        self._save_improvements()
        return result

    def _generate_improvement(
        self, mod_path: Path, suggestion: ImprovementSuggestion
    ) -> Optional[str]:
        """用 LLM 生成改进代码"""
        try:
            from hongjun.gateway.server import _get_llm

            llm = _get_llm()
            if not llm:
                return None

            current_code = mod_path.read_text(encoding="utf-8")
            # 截断避免 token 溢出
            if len(current_code) > 4000:
                current_code = current_code[:4000] + "\n# ... (truncated)"

            prompt = f"""你是鸿钧 AI Agent 的代码改进助手。
当前模块 `{mod_path.name}` 存在以下问题：

问题类型: {suggestion.improvement_type}
优先级: {'高' if suggestion.priority == 1 else '中' if suggestion.priority == 2 else '低'}
描述: {suggestion.description}
当前代码片段: {suggestion.current_snippet}
建议改进: {suggestion.suggested_fix}

请生成改进后的完整代码。规则：
1. 只修改必要部分，不要重写整个文件
2. 保持现有接口不变（不改变公开函数签名）
3. 添加适当的 docstring 和注释
4. 直接输出完整代码（用 ```python ``` 包裹）
"""

            resp = llm.chat_sync([
                {"role": "system", "content": "你是鸿钧的代码改进助手。"},
                {"role": "user", "content": prompt},
            ])

            content = resp.content if hasattr(resp, "content") else str(resp)

            # 提取代码块
            import re
            blocks = re.findall(r"```python\n(.*?)```", content, re.DOTALL)
            if blocks:
                return blocks[0].strip()

            # 没有 markdown 代码块 → 尝试直接返回
            if "def " in content:
                return content.strip()

            return None
        except Exception as e:
            logger.warning(f"生成改进代码失败: {e}")
            return None

    def _health_check(self) -> bool:
        """健康检查"""
        try:
            result = subprocess.run(
                [
                    "/home/asus/.venv/bin/python",
                    "/home/asus/hongjun/scripts/self_check.py",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd="/home/asus/hongjun",
                env={"PYTHONPATH": "/home/asus/hongjun/src"},
            )
            return result.returncode == 0
        except Exception:
            return False

    def _git_commit_prestate(self, mod_path: Path) -> Optional[str]:
        """提交当前状态（便于回滚）"""
        try:
            r = subprocess.run(
                ["git", "add", str(mod_path)],
                capture_output=True,
                text=True,
                cwd="/home/asus/hongjun",
            )
            r = subprocess.run(
                [
                    "git", "commit", "-m",
                    f"wip: pre-improvement snapshot {mod_path.stem}",
                ],
                capture_output=True,
                text=True,
                cwd="/home/asus/hongjun",
            )
            if r.returncode == 0:
                sha = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    cwd="/home/asus/hongjun",
                )
                return sha.stdout.strip()
        except Exception:
            pass
        return None

    def _git_commit_improvement(self, mod_path: Path, suggestion: ImprovementSuggestion):
        """提交改进"""
        try:
            subprocess.run(
                ["git", "add", str(mod_path)],
                capture_output=True,
                text=True,
                cwd="/home/asus/hongjun",
            )
            subprocess.run(
                [
                    "git", "commit", "-m",
                    f"improve({suggestion.improvement_type}): {suggestion.module} — {suggestion.description[:80]}",
                ],
                capture_output=True,
                text=True,
                cwd="/home/asus/hongjun",
            )
            # 推送到 GitHub
            subprocess.run(
                ["git", "push", "origin", "main"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd="/home/asus/hongjun",
            )
            logger.info(f"改进已推送到 GitHub: {mod_path.stem}")
        except Exception as e:
            logger.warning(f"git push 失败: {e}")

    # ── 统计 ───────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取改进统计"""
        applied = [r for r in self._improvements if r.applied]
        passed = [r for r in self._improvements if r.verification_passed]
        return {
            "total": len(self._improvements),
            "applied": len(applied),
            "verification_passed": len(passed),
            "pass_rate": round(len(passed) / len(applied), 2) if applied else 0,
            "recent": [
                {
                    "module": r.suggestion.module,
                    "description": r.suggestion.description[:60],
                    "passed": r.verification_passed,
                    "timestamp": r.timestamp,
                }
                for r in self._improvements[:5]
            ],
        }
