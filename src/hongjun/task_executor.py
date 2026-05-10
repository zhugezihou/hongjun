"""
鸿钧 · 任务执行器（含交叉验证闭环）
=====================================

执行 TaskStep 并在每步后验证结果。

验证策略：
  - 每步执行后立即验证（不让错误蔓延）
  - 验证失败 → 重试（最多max_retries次）
  - 重试失败 → 标记失败，可选择跳过或终止
  - 所有步骤完成后 → 交叉验证整体结果

与 self_evolution.py 的区别：
  - self_evolution：代码级执行+验证（生成→执行→重试）
  - task_executor：任务级执行+验证（步骤→验证→决策）
"""

from __future__ import annotations
import json
import subprocess
from datetime import datetime
from typing import Optional

from hongjun.logging_config import get_logger
from .planner import TaskStep, StepStatus, ExecutionPlan
from .llm import chat_sync, LLMResponse

logger = get_logger("hongjun.task_executor")


# ── 验证器 ─────────────────────────────────────────────────────────────────

class StepVerifier:
    """
    步骤结果验证器。

    策略：
      - 通过 LLM 判断当前步骤结果是否满足 verification 条件
      - 如果没有 verification，用规则做基本检查
    """

    def __init__(self, llm_model: str = "MiniMax-M2.7"):
        self.llm_model = llm_model

    def verify(self, step: TaskStep, context: dict = None) -> tuple[bool, str]:
        """
        Returns:
            (is_valid, reason)
        """
        context = context or {}

        # 规则验证（基础）
        if step.tool_name == "executor":
            exit_code = step.tool_args.get("exit_code", 0)
            if exit_code != 0:
                return False, f"执行失败，退出码 {exit_code}"

        # 有 verification 字符串 → LLM 判断
        if step.verification:
            return self._llm_verify(step, context)

        # 无 verification → 基础检查
        if step.result:
            return True, "步骤完成"
        return False, "步骤无结果"

    def _llm_verify(self, step: TaskStep, context: dict) -> tuple[bool, str]:
        """用 LLM 判断验证条件是否满足"""
        prompt = f"""你是一个步骤验证器。判断当前步骤是否成功完成。

步骤描述：{step.description}
步骤结果：{step.result or '(无结果)'}
验证条件：{step.verification}

只输出：
PASS - 原因（简短）
FAIL - 原因（简短）
"""
        try:
            resp: LLMResponse = chat_sync(
                messages=[{"role": "user", "content": prompt}],
                model=self.llm_model,
                temperature=0,
                max_tokens=128,
            )
            content = resp.content.strip() if resp.content else "FAIL - 无响应"
            is_pass = content.startswith("PASS")
            reason = content[5:] if is_pass else content[5:]
            return is_pass, reason
        except Exception as e:
            logger.warning(f"LLM 验证失败: {e}")
            return False, f"验证异常: {e}"


# ── 单步执行器 ──────────────────────────────────────────────────────────────

class TaskExecutor:
    """
    任务执行器：执行计划中的单个步骤，并验证结果。

    使用方式：
        executor = TaskExecutor()
        result = executor.execute_step(step, context)
    """

    def __init__(self, llm_model: str = "MiniMax-M2.7"):
        self.llm_model = llm_model
        self.verifier = StepVerifier(llm_model)

    def execute_step(
        self,
        step: TaskStep,
        context: dict = None,
    ) -> TaskStep:
        """执行单个步骤并验证。"""
        context = context or {}
        step.mark_running()
        logger.info(f"▶️  执行步骤 [{step.step_id}]: {step.description}")

        try:
            if step.tool_name == "llm":
                step = self._execute_llm_step(step, context)
            elif step.tool_name == "shell":
                step = self._execute_shell_step(step)
            elif step.tool_name == "browser":
                step.result = "[浏览器操作，需要通过 skill 执行]"
            elif step.tool_name == "executor":
                step = self._execute_code_step(step)
            elif step.tool_name == "memory":
                step = self._execute_memory_step(step, context)
            elif step.tool_name == "reflection":
                step = self._execute_reflection_step(step)
            else:
                step.mark_failed(f"未知工具: {step.tool_name}")
                return step

            # 验证步骤结果
            is_valid, reason = self.verifier.verify(step, context)
            if is_valid:
                step.mark_completed(step.result or "完成")
                logger.info(f"✅ 步骤完成 [{step.step_id}]: {reason}")
            else:
                if step.can_retry():
                    step.retry_count += 1
                    logger.warning(f"⚠️  验证失败 [{step.step_id}]，重试 {step.retry_count}/{step.max_retries}: {reason}")
                    context["last_error"] = reason
                    return self.execute_step(step, context)
                else:
                    step.mark_failed(f"验证失败（已重试{step.max_retries}次）: {reason}")
                    logger.error(f"❌ 步骤失败 [{step.step_id}]: {reason}")

        except Exception as e:
            if step.can_retry():
                step.retry_count += 1
                step.error = str(e)
                logger.warning(f"⚠️  执行异常 [{step.step_id}]，重试 {step.retry_count}/{step.max_retries}: {e}")
                return self.execute_step(step, context)
            else:
                step.mark_failed(str(e))
                logger.error(f"❌ 步骤异常 [{step.step_id}]: {e}")

        return step

    def _execute_llm_step(self, step: TaskStep, context: dict) -> TaskStep:
        """LLM 调用步骤"""
        prompt = step.tool_args.get("prompt", step.description)
        system_msg = step.tool_args.get("system", "")

        messages = []
        if system_msg:
            messages.append({"role": "system", "content": system_msg})
        messages.append({"role": "user", "content": prompt})

        # 注入相关记忆上下文
        if context.get("memory_context"):
            marker = "[HongjunMemoryContext]\n"
            mem_msg = {"role": "system", "content": marker + context["memory_context"]}
            if system_msg:
                messages.insert(1, mem_msg)
            else:
                messages.insert(0, mem_msg)

        try:
            resp: LLMResponse = chat_sync(
                messages=messages,
                model=self.llm_model,
                temperature=step.tool_args.get("temperature", 0.7),
                max_tokens=step.tool_args.get("max_tokens", 4096),
            )
            step.result = resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:
            step.result = ""
            raise RuntimeError(f"LLM 调用失败: {e}")
        return step

    def _execute_shell_step(self, step: TaskStep) -> TaskStep:
        """Shell 命令执行步骤"""
        cmd = step.tool_args.get("command")
        timeout = step.tool_args.get("timeout", 60)
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            step.result = result.stdout.strip() or result.stderr.strip()
            step.tool_args["exit_code"] = result.returncode
            if result.returncode != 0:
                raise RuntimeError(f"Shell 命令失败，退出码 {result.returncode}")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Shell 命令超时（{timeout}s）")
        except Exception as e:
            raise e
        return step

    def _execute_code_step(self, step: TaskStep) -> TaskStep:
        """代码执行步骤"""
        code = step.tool_args.get("code", "")
        language = step.tool_args.get("language", "python")
        try:
            from hongjun.self_evolution import CodeExecutor
            executor = CodeExecutor()
            exec_result = executor.execute(code, language)
            step.result = exec_result.stdout or exec_result.stderr
            step.tool_args["exit_code"] = exec_result.exit_code
        except Exception as e:
            raise RuntimeError(f"代码执行失败: {e}")
        return step

    def _execute_memory_step(self, step: TaskStep, context: dict) -> TaskStep:
        """记忆检索步骤"""
        query = step.tool_args.get("query", step.description)
        try:
            from hongjun.evolution_memory import EvolutionMemory
            mem = EvolutionMemory()
            results = mem.search(query, limit=3)
            if results:
                step.result = "\n".join(
                    f"- {r.get('task', '')}: {r.get('result_preview', '')[:100]}"
                    for r in results
                )
            else:
                step.result = "未找到相关记忆"
        except Exception as e:
            step.result = f"记忆检索失败: {e}"
        return step

    def _execute_reflection_step(self, step: TaskStep) -> TaskStep:
        """反思步骤"""
        try:
            from hongjun.reflection_engine import get_reflection_engine
            engine = get_reflection_engine()
            result = engine.daily_reflection()
            step.result = result.summary
        except Exception as e:
            step.result = f"反思失败: {e}"
        return step


# ── 计划执行器 ──────────────────────────────────────────────────────────────

class PlanExecutor:
    """
    完整计划执行器：按顺序执行计划中的所有步骤，每步验证后继续。

    使用方式：
        plan_executor = PlanExecutor()
        final_plan = plan_executor.execute_plan(plan, context)
    """

    def __init__(self, llm_model: str = "MiniMax-M2.7"):
        self.executor = TaskExecutor(llm_model)

    def execute_plan(
        self,
        plan: ExecutionPlan,
        context: dict = None,
    ) -> ExecutionPlan:
        """执行完整计划。"""
        context = context or {}
        plan.status = plan.status.EXECUTING
        logger.info(f"🚀 开始执行计划 [{plan.plan_id}]: {plan.user_request[:60]}")

        while plan.current_step_index < len(plan.steps):
            step = plan.current_step()

            if step.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                plan.advance()
                continue

            updated_step = self.executor.execute_step(step, context)
            plan.steps[plan.current_step_index] = updated_step

            if updated_step.status == StepStatus.FAILED:
                if not updated_step.can_retry() and updated_step.retry_count >= updated_step.max_retries:
                    logger.error(f"❌ 步骤 [{step.step_id}] 失败且不可重试，终止计划")
                    plan.status = plan.status.FAILED
                    plan.final_result = f"步骤 [{step.description}] 执行失败: {updated_step.error}"
                    return plan

            plan.advance()

        # 交叉验证
        plan.status = plan.status.VALIDATING
        cross_valid = self._cross_validate(plan, context)
        plan.verification_result = cross_valid[1]

        if plan.is_complete() and cross_valid[0]:
            plan.status = plan.status.COMPLETED
            plan.final_result = self._summarize_plan(plan)
            plan.completed_at = datetime.now().isoformat()
            logger.info(f"✅ 计划完成 [{plan.plan_id}]")
        else:
            plan.status = plan.status.FAILED
            plan.final_result = f"交叉验证失败: {cross_valid[1]}"

        return plan

    def _cross_validate(self, plan: ExecutionPlan, context: dict) -> tuple[bool, str]:
        """交叉验证"""
        step_results = "\n".join(
            f"[{s.step_id.split('_')[-1]}] {s.description}: {s.status.value}"
            + (f" → {s.result[:100]}" if s.result else "")
            for s in plan.steps
        )
        prompt = f"""你是鸿钧的交叉验证器。

用户请求：{plan.user_request}
步骤执行摘要：
{step_results}

请判断：
1. 所有关键步骤是否完成？
2. 最终结果是否满足用户意图？

只输出：
PASS - 简短原因
FAIL - 简短原因
"""
        try:
            resp: LLMResponse = chat_sync(
                messages=[{"role": "user", "content": prompt}],
                model=self.executor.llm_model,
                temperature=0,
                max_tokens=64,
            )
            content = resp.content.strip() if resp.content else "FAIL - 无响应"
            is_pass = content.startswith("PASS")
            reason = content[5:] if is_pass else content[5:]
            return is_pass, reason
        except Exception as e:
            return False, f"交叉验证异常: {e}"

    def _summarize_plan(self, plan: ExecutionPlan) -> str:
        """汇总计划执行结果"""
        lines = [f"📋 计划执行完成 [{plan.plan_id}]", f"用户请求: {plan.user_request}", "", "执行摘要:"]
        for s in plan.steps:
            icon = {"completed": "✅", "failed": "❌", "skipped": "⏭️"}.get(s.status.value, "⬜")
            lines.append(f"  {icon} [{s.description}]")
            if s.result:
                lines.append(f"     → {s.result[:150]}")
        lines.append("")
        lines.append(f"交叉验证: {plan.verification_result}")
        return "\n".join(lines)
