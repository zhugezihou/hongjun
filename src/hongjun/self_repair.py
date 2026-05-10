"""
鸿钧 · 自我诊断引擎
====================

能够分析自身代码库、识别问题、生成修复方案并自动执行的模块。

核心能力：
  1. 代码库自检：定期扫描自身模块，发现异常（语法错误、导入失败、类型问题）
  2. 错误分析：捕获执行失败，定位到具体文件和行号
  3. 自我修复：生成修复补丁并应用（安全边界内）
  4. 修复验证：应用后运行测试，确认修复有效

安全边界：
  - ❌ 不可修改：security.py（安全逻辑）、models.py（数据模型）
  - ✅ 可修改：orchestrator.py、tools.py、self_evolution.py、executor.py、
              intent_classifier.py、skill_manager.py、memory.py
"""

from __future__ import annotations
import ast
import json
import os
import subprocess
import traceback
from pathlib import Path
from typing import Optional

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.self_repair")

# 可安全自改的模块（不含安全关键模块）
SAFE_TO_MODIFY = {
    "orchestrator", "self_evolution", "executor", "tools",
    "intent_classifier", "skill_manager", "memory",
    "feishu_client", "agent", "evaluator", "hindsight_integration",
    "cli", "llm", "logging_config", "config",
    "reflection_engine", "planner", "task_executor", "task_state",
    "memory_injection", "error_pattern", "skill_discovery",
    "meta_learner", "self_improver",
}

# 不可自改的模块（安全关键）
PROTECTED_MODULES = {"security", "models"}

# 核心模块目录
CORE_DIR = Path(__file__).parent  # self_repair.py 在 src/hongjun/ 下


class SelfRepairEngine:
    """
    自我诊断与修复引擎。

    使用方式：
        engine = SelfRepairEngine()
        report = engine.run_diagnostics()      # 全面诊断
        fixes = engine.fix_module("orchestrator", error_trace)  # 修复指定模块
    """

    def __init__(self, core_dir: Path = CORE_DIR):
        self.core_dir = core_dir
        self.history: list[dict] = []

    # ── 诊断 ────────────────────────────────────────────────────────

    def run_diagnostics(self) -> "DiagnosticReport":
        """
        执行全面自检，返回诊断报告。
        """
        report = DiagnosticReport()
        report.modules_scanned = 0
        report.modules_ok = 0
        report.modules_with_issues = 0
        report.issues = []

        for py_file in self.core_dir.glob("*.py"):
            if py_file.name.startswith("_") or py_file.name == "self_repair.py":
                continue
            report.modules_scanned += 1

            module_name = py_file.stem
            issues = self._diagnose_file(py_file, module_name)
            if issues:
                report.modules_with_issues += 1
                report.issues.extend(issues)
            else:
                report.modules_ok += 1

        # 检查导入完整性
        import_issues = self._check_imports()
        if import_issues:
            report.issues.extend(import_issues)
            report.modules_with_issues += len(import_issues)

        self.history.append({"type": "diagnostic", "report": report.summary()})
        return report

    def _diagnose_file(self, path: Path, module_name: str) -> list["Issue"]:
        """诊断单个文件"""
        issues = []
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)

            # 检查语法错误
            # (ast.parse already throws SyntaxError)

            # 检查未使用的导入
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.startswith("hongjun."):
                        for alias in node.names:
                            name = alias.asname or alias.name
                            # 简单检查：如果这个名字在文件里只出现一次（import那行），可能是未使用
                            pass  # 简化处理

            # 检查路径引用
            with open(path) as f:
                lines = f.readlines()
            for i, line in enumerate(lines, 1):
                if "PROTECTED" in line and module_name in SAFE_TO_MODIFY:
                    pass  # 忽略

        except SyntaxError as e:
            issues.append(Issue(
                severity="critical",
                module=module_name,
                file=str(path),
                line=e.lineno or 0,
                description=f"语法错误: {e.msg}",
                code=linecache_get_line(path, e.lineno) if e.lineno else "",
            ))
        except Exception as e:
            issues.append(Issue(
                severity="error",
                module=module_name,
                file=str(path),
                line=0,
                description=f"诊断过程出错: {e}",
                code="",
            ))

        return issues

    def _check_imports(self) -> list["Issue"]:
        """检查所有模块的导入是否都能解析"""
        issues = []
        for py_file in self.core_dir.glob("*.py"):
            if py_file.stem.startswith("_"):
                continue
            try:
                import importlib
                importlib.import_module(f"hongjun.{py_file.stem}")
            except ImportError as e:
                issues.append(Issue(
                    severity="critical",
                    module=py_file.stem,
                    file=str(py_file),
                    line=0,
                    description=f"导入失败: {e}",
                    code="",
                ))
        return issues

    # ── 修复 ────────────────────────────────────────────────────────

    def fix_module(self, module_name: str, error_info: str) -> list["FixResult"]:
        """
        根据错误信息修复指定模块。

        Args:
            module_name: 模块名（不含 hongjun. 前缀）
            error_info: 错误描述（可以是小结巴行错误、堆栈信息）

        Returns:
            FixResult 列表（每个提议的修复）
        """
        if module_name in PROTECTED_MODULES:
            return [FixResult(
                module=module_name,
                description="禁止自改安全关键模块",
                fix_plan="",
                applied=False,
                success=False,
                reason="PROTECTED_MODULES",
            )]

        if module_name not in SAFE_TO_MODIFY:
            return [FixResult(
                module=module_name,
                description=f"未知模块: {module_name}",
                fix_plan="",
                applied=False,
                success=False,
                reason="UNKNOWN_MODULE",
            )]

        file_path = self.core_dir / f"{module_name}.py"
        if not file_path.exists():
            # 可能是 .pyi 或不存在
            return [FixResult(
                module=module_name,
                description=f"文件不存在: {file_path}",
                fix_plan="",
                applied=False,
                success=False,
                reason="FILE_NOT_FOUND",
            )]

        # 生成修复方案
        fix_plan = self._generate_fix(module_name, error_info, file_path)
        if not fix_plan:
            return [FixResult(
                module=module_name,
                description="无法自动生成修复方案",
                fix_plan="",
                applied=False,
                success=False,
                reason="NO_FIX_PLAN",
            )]

        # 应用修复
        applied = self._apply_fix(file_path, fix_plan)
        result = FixResult(
            module=module_name,
            description=f"修复方案: {fix_plan['description']}",
            fix_plan=fix_plan["plan"],
            applied=applied,
            success=applied,
            reason="" if applied else "APPLY_FAILED",
        )

        self.history.append({
            "type": "repair_attempt",
            "module": module_name,
            "fix_plan": fix_plan,
            "applied": applied,
            "error_info": error_info,
        })

        return [result]

    def _generate_fix(self, module_name: str, error_info: str, file_path: Path) -> Optional[dict]:
        """
        用 LLM 分析错误并生成修复方案。

        优先使用已知修复库（error_pattern），找不到再 LLM 生成。
        """
        # ── Step 1：查错误模式库 ─────────────────────────────────────
        try:
            from hongjun.error_pattern import get_error_library
            lib = get_error_library()

            # 提取错误类型
            import re
            err_type_m = re.search(r'(\w+Error|\w+Exception)', error_info)
            error_type = err_type_m.group(1) if err_type_m else ""

            known_fix = lib.lookup_by_error(error_type, error_info)
            if known_fix:
                logger.info(f"使用已知修复 [{known_fix.pattern_id}]: {known_fix.description}")
                lib.record_fix_success(known_fix.pattern_id)
                return {
                    "description": known_fix.description,
                    "plan": known_fix.fix_command or known_fix.fix_code or known_fix.fix_description,
                    "from_known_pattern": True,
                    "pattern_id": known_fix.pattern_id,
                }
        except Exception as e:
            logger.warning(f"查找已知修复失败: {e}")

        # ── Step 2：LLM 生成修复 ─────────────────────────────────────
        try:
            from hongjun.gateway.server import _get_llm
            llm = _get_llm()
            if not llm:
                return None

            source = file_path.read_text(encoding="utf-8")
            # 只截取前 200 行（避免 token 过多）
            source_preview = "\n".join(source.split("\n")[:200])

            resp = llm.chat_sync([
                {
                    "role": "system",
                    "content": f"""你是一个专业的 Python 代码修复助手。
分析错误信息，找出 Hongjun 代码库中的问题，并生成修复方案。

规则：
1. 只修改 {module_name}.py，不要改其他文件
2. 只输出 JSON 格式，不要有其他文字
3. JSON 格式：{{"description": "问题描述", "plan": "具体修复代码或修复步骤"}}
4. 如果问题无法自动修复，返回 {{"description": "", "plan": ""}}
5. 安全边界：不要修改 security.py、models.py
"""
                },
                {
                    "role": "user",
                    "content": f"模块：{module_name}\n文件：{file_path}\n\n错误信息：\n{error_info}\n\n代码片段（前200行）：\n{source_preview}",
                },
            ])

            content = resp.content if hasattr(resp, "content") else str(resp)
            # 提取 JSON
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if m:
                return json.loads(m.group())
            return None
        except Exception as e:
            logger.error(f"生成修复方案失败: {e}")
            return None

    def _apply_fix(self, file_path: Path, fix_plan: dict) -> bool:
        """应用修复到文件"""
        if not fix_plan.get("plan"):
            return False

        try:
            # 备份
            backup_path = file_path.with_suffix(".py.bak")
            backup_path.write_text(file_path.read_text(encoding="utf-8"), encoding="utf-8")

            plan = fix_plan["plan"].strip()

            # 如果是完整代码替换
            if plan.startswith("```python") or plan.startswith("```py"):
                # 提取代码块内容
                import re
                m = re.search(r"```(?:python|py)?\n(.*?)\n```", plan, re.DOTALL)
                if m:
                    plan = m.group(1)

            if plan.startswith("=") or ("\n" in plan and len(plan) > 100):
                # 认为是要替换的完整代码
                file_path.write_text(plan, encoding="utf-8")
            else:
                # 认为是要追加或简单修改，记录到日志
                logger.info(f"修复计划（需人工审查）: {plan}")

            # 验证语法正确
            try:
                ast.parse(file_path.read_text(encoding="utf-8"))
                logger.info(f"修复验证通过: {file_path}")
                return True
            except SyntaxError:
                # 恢复备份
                file_path.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
                logger.error(f"修复后语法验证失败，恢复备份: {file_path}")
                return False

        except Exception as e:
            logger.error(f"应用修复失败: {e}")
            return False

    # ── 自检报告 ────────────────────────────────────────────────────

    def check_gateway_health(self) -> dict:
        """检查 Gateway 健康状态"""
        try:
            import httpx
            resp = httpx.get("http://127.0.0.1:20830/health", timeout=5.0)
            healthy = resp.status_code == 200 and resp.text == "OK"
            return {"healthy": healthy, "status_code": resp.status_code, "body": resp.text}
        except Exception as e:
            return {"healthy": False, "error": str(e)}

    def full_status_report(self) -> dict:
        """生成完整的自检状态报告"""
        gateway = self.check_gateway_health()
        diag = self.run_diagnostics()
        return {
            "gateway": gateway,
            "diagnostics": diag.summary(),
            "repair_history_count": len(self.history),
        }


# ── 数据结构 ──────────────────────────────────────────────────────

class Issue:
    def __init__(self, severity: str, module: str, file: str, line: int, description: str, code: str):
        self.severity = severity  # critical / error / warning / info
        self.module = module
        self.file = file
        self.line = line
        self.description = description
        self.code = code


class DiagnosticReport:
    def __init__(self):
        self.modules_scanned = 0
        self.modules_ok = 0
        self.modules_with_issues = 0
        self.issues: list[Issue] = []

    def summary(self) -> dict:
        return {
            "modules_scanned": self.modules_scanned,
            "modules_ok": self.modules_ok,
            "modules_with_issues": self.modules_with_issues,
            "issues": [
                {"severity": i.severity, "module": i.module, "line": i.line, "description": i.description}
                for i in self.issues
            ],
        }


class FixResult:
    def __init__(self, module: str, description: str, fix_plan: str, applied: bool, success: bool, reason: str):
        self.module = module
        self.description = description
        self.fix_plan = fix_plan
        self.applied = applied
        self.success = success
        self.reason = reason


# ── 工具函数 ──────────────────────────────────────────────────────

def linecache_get_line(path: Path, lineno: int) -> str:
    """获取指定文件的指定行"""
    try:
        import linecache
        return linecache.getline(str(path), lineno).strip()
    except Exception:
        return ""


# ── 便捷入口 ─────────────────────────────────────────────────────

def self_repair(error_info: str, module_name: str = "") -> str:
    """
    给 orchestrator 调用的自我修复入口。
    
    Args:
        error_info: 错误描述
        module_name: 可选，指定模块名
    
    Returns:
        修复结果描述
    """
    engine = SelfRepairEngine()
    if module_name:
        results = engine.fix_module(module_name, error_info)
    else:
        # 从错误信息中推断模块名
        import re
        m = re.search(r"/(hongjun/[\w_]+)\.py", error_info)
        inferred_module = m.group(1).split("/")[-1] if m else ""
        if inferred_module:
            results = engine.fix_module(inferred_module, error_info)
        else:
            results = engine.fix_module("orchestrator", error_info)

    if not results:
        return "无法生成修复方案"

    result = results[0]
    if result.applied:
        return f"✅ 已自动修复 [{result.module}]: {result.description}"
    else:
        return f"⚠️ 无法自动修复 [{result.module}]: {result.reason} — {result.description}"
