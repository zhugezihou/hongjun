"""
鸿钧 · 自我进化引擎
====================

任务完成后的自我验证 + 执行反馈回路。

流程：
  生成结果 → 验证质量 → 执行代码 → 检查输出 → 失败则重试（最多2次）

Phase 1（当前）：
  - 代码类任务：生成后自动执行，返回运行结果而非代码字符串
  - 执行失败：分析错误，重新生成，再次执行
  - 验证通过：返回实际结果

Phase 2（规划中）：
  - 截图验证：HTML/JS 生成物自动截图确认视觉效果
  - 自我修改：识别自身代码 bug，主动修复

Phase 3（规划中）：
  - 持续进化：记录进化历史，主动学习新技能
"""

from __future__ import annotations
import base64
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

# 复用已有的日志和工具
from hongjun.logging_config import get_logger

logger = get_logger("hongjun.self_evolution")


# ── 结果质量验证器 ────────────────────────────────────────────────

class ResultVerifier:
    """
    验证任务执行结果的质量。
    
    检查维度：
    - 执行是否成功（退出码）
    - 输出是否为空
    - 输出是否包含错误关键词
    - 输出是否包含用户期望的关键词
    """

    ERROR_PATTERNS = [
        "syntax error", "import error", "module not found",
        "permission denied", "no such file", "connection refused",
        "timeout", "traceback", "error:", "failed", "Exception",
    ]

    def __init__(self, user_intent: str):
        self.user_intent = user_intent.lower()
        self.attempts = 0
        self.max_attempts = 2

    def verify(self, result: "ExecutionResult") -> "VerificationResult":
        """
        验证执行结果，返回 (is_satisfactory, reason, suggestion)。
        """
        self.attempts += 1

        # 检查退出码
        if result.exit_code != 0:
            return VerificationResult(
                is_satisfactory=False,
                reason=f"执行失败，退出码 {result.exit_code}",
                suggestion=self._extract_error_suggestion(result),
                retry=True,
            )

        # 检查输出是否为空
        if not result.stdout.strip() and not result.stderr.strip():
            return VerificationResult(
                is_satisfactory=False,
                reason="执行成功但无输出",
                suggestion="请确保程序产生可见输出",
                retry=True,
            )

        # 检查是否包含错误信息
        combined = (result.stdout + result.stderr).lower()
        error_found = any(pat in combined for pat in self.ERROR_PATTERNS)
        if error_found:
            return VerificationResult(
                is_satisfactory=False,
                reason="输出中包含错误信息",
                suggestion=self._extract_error_suggestion(result),
                retry=True,
            )

        # 检查输出长度是否合理（太短可能是异常退出）
        if len(result.stdout.strip()) < 5:
            return VerificationResult(
                is_satisfactory=False,
                reason="输出太短，可能执行异常",
                suggestion="确保程序产生足够的输出",
                retry=True,
            )

        # 基本质量通过
        return VerificationResult(
            is_satisfactory=True,
            reason="执行成功，输出质量正常",
            suggestion="",
            retry=False,
        )

    def _extract_error_suggestion(self, result: ExecutionResult) -> str:
        """从错误输出中提取有用的建议"""
        output = result.stderr or result.stdout
        # 提取第一行 Python 错误
        lines = [l.strip() for l in output.split("\n") if l.strip()]
        for line in lines:
            if "error" in line.lower() or "traceback" in line.lower():
                # 截断过长行
                return line[:200]
        return lines[0][:200] if lines else "未知错误"


class VerificationResult:
    def __init__(self, is_satisfactory: bool, reason: str, suggestion: str, retry: bool):
        self.is_satisfactory = is_satisfactory
        self.reason = reason
        self.suggestion = suggestion
        self.retry = retry


class ExecutionResult:
    def __init__(self, stdout: str, stderr: str, exit_code: int, file_path: str = ""):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.file_path = file_path


# ── 代码提取器 ────────────────────────────────────────────────────

def extract_code_blocks(text: str) -> list[dict]:
    """
    从 LLM 输出中提取代码块。
    
    Returns:
        [{"language": "python", "code": "...", "start": 0, "end": 100}, ...]
    """
    blocks = []
    # 匹配 markdown 代码块：```python\n...\n```
    pattern = r"```(\w*)\n(.*?)```"
    for m in re.finditer(pattern, text, re.DOTALL):
        lang = m.group(1).lower() or "text"
        code = m.group(2)
        blocks.append({
            "language": lang,
            "code": code,
            "start": m.start(),
            "end": m.end(),
        })
    return blocks


def detect_output_type(user_intent: str) -> str:
    """
    从用户意图判断期望的输出类型。
    
    Returns:
        "executable" - 需要执行并返回结果
        "html"       - 生成 HTML 并打开
        "text"       - 纯文本输出
        "visual"     - 可视化结果（需要截图验证）
    """
    intent_lower = user_intent.lower()
    
    visual_keywords = [
        "动画", "动画效果", "游戏", "可视化", "图表", "matrix", "matrix动画",
        "粒子", "粒子效果", "canvas", "webgl", "three.js", "d3",
        "生成图片", "画图", "图形", "界面", "UI", "展示", "演示",
    ]
    html_keywords = [
        "html", "网页", "网站", "前端", "页面", "css", "javascript",
        "交互", "浏览器", "web", "单页",
    ]
    
    if any(kw in intent_lower for kw in visual_keywords):
        return "visual"
    if any(kw in intent_lower for kw in html_keywords):
        return "html"
    if any(kw in intent_lower for kw in ["python", "py ", "执行", "运行结果"]):
        return "executable"
    
    # 默认：生成并执行
    return "executable"


# ── 代码执行器 ────────────────────────────────────────────────────

class CodeExecutor:
    """
    自动执行代码并返回结果。
    
    支持：Python、JavaScript(Node)、HTML(截图验证)
    """
    
    def __init__(self, workspace: str = "/tmp/hongjun_exec"):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._node_cache: Optional[str] = None

    def execute(self, code: str, language: str, user_intent: str = "") -> ExecutionResult:
        """
        执行代码，返回结果。
        
        Args:
            code: 代码内容
            language: 语言 (python/js/html/shell)
            user_intent: 用户原始意图（用于判断输出类型）
        """
        if language in ("python", "py"):
            return self._execute_python(code)
        elif language in ("javascript", "js", "node"):
            return self._execute_node(code)
        elif language in ("html", "htm"):
            return self._execute_html(code, user_intent)
        elif language in ("shell", "bash", "sh"):
            return self._execute_shell(code)
        else:
            return ExecutionResult(
                stdout="",
                stderr=f"不支持的语言: {language}",
                exit_code=1,
            )

    def _execute_python(self, code: str) -> ExecutionResult:
        """执行 Python 代码"""
        # 写入临时文件（避免 heredoc 复杂转义问题）
        file_id = uuid.uuid4().hex[:8]
        file_path = self.workspace / f"exec_{file_id}.py"
        file_path.write_text(code, encoding="utf-8")
        
        try:
            result = subprocess.run(
                ["python3", str(file_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            return ExecutionResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                file_path=str(file_path),
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult("", "执行超时（60s）", 124, str(file_path))
        except Exception as e:
            return ExecutionResult("", str(e), 1, str(file_path))

    def _execute_node(self, code: str) -> ExecutionResult:
        """执行 JavaScript (Node.js)"""
        if not self._node_available():
            return ExecutionResult("", "Node.js 未安装", 127, "")
        
        file_id = uuid.uuid4().hex[:8]
        file_path = self.workspace / f"exec_{file_id}.js"
        file_path.write_text(code, encoding="utf-8")
        
        try:
            result = subprocess.run(
                ["node", str(file_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return ExecutionResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                file_path=str(file_path),
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult("", "执行超时（30s）", 124, str(file_path))
        except Exception as e:
            return ExecutionResult("", str(e), 1, str(file_path))

    def _execute_html(self, code: str, user_intent: str = "") -> ExecutionResult:
        """
        执行 HTML：保存文件并尝试截图验证。
        
        如果有 playwright/selenium，截图保存；否则返回文件路径。
        """
        file_id = uuid.uuid4().hex[:8]
        file_path = self.workspace / f"output_{file_id}.html"
        file_path.write_text(code, encoding="utf-8")
        
        # 尝试截图验证
        screenshot_path = self.workspace / f"screenshot_{file_id}.png"
        screenshot_taken = False
        
        # 优先用 playwright 截图
        screenshot_taken = self._try_playwright_screenshot(str(file_path), str(screenshot_path))
        
        if screenshot_taken and screenshot_path.exists():
            # 返回截图路径和 HTML 文件路径
            size = screenshot_path.stat().st_size
            return ExecutionResult(
                stdout=f"✅ HTML 执行成功！\n📄 文件位置：file://{file_path}\n🖼️ 截图：file://{screenshot_path}\n📦 截图大小：{size} bytes\n\n（截图已保存，可直接在浏览器打开 HTML 文件查看效果）",
                stderr="",
                exit_code=0,
                file_path=str(file_path),
            )
        else:
            # 无截图工具，返回文件路径
            return ExecutionResult(
                stdout=f"✅ HTML 生成完成！\n📄 文件：file://{file_path}\n\n请在浏览器中打开查看效果。",
                stderr="",
                exit_code=0,
                file_path=str(file_path),
            )

    def _try_playwright_screenshot(self, html_path: str, screenshot_path: str) -> bool:
        """尝试用 Playwright 截图，成功返回 True"""
        try:
            import playwright
            # 用 playwright 打开并截图
            script = f"""
const{{ chromium }} = require('playwright');
(async()=>{{
    const browser = await chromium.launch();
    const page = await browser.newPage();
    await page.goto('file://{html_path}');
    await page.waitForTimeout(2000);
    await page.screenshot({{path:'{screenshot_path}'}});
    await browser.close();
}})();
"""
            result = subprocess.run(
                ["node", "-e", script],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "NODE_PATH": "/home/asus/.npm-global/lib/node_modules"},
            )
            return result.returncode == 0 and Path(screenshot_path).exists()
        except Exception:
            return False

    def _execute_shell(self, code: str) -> ExecutionResult:
        """执行 Shell 命令"""
        # 去掉可能的 ```shell 包裹
        code = re.sub(r"^```\w*\n?", "", code.strip())
        code = re.sub(r"\n?```$", "", code)
        
        try:
            result = subprocess.run(
                code,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return ExecutionResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult("", "执行超时（60s）", 124, "")
        except Exception as e:
            return ExecutionResult("", str(e), 1, "")

    def _node_available(self) -> bool:
        """检查 Node.js 是否可用"""
        if self._node_cache is not None:
            return self._node_cache
        try:
            r = subprocess.run(["node", "--version"], capture_output=True, timeout=5)
            self._node_cache = r.returncode == 0
        except Exception:
            self._node_cache = False
        return self._node_cache


# ── 自我进化主循环 ─────────────────────────────────────────────────

class SelfEvolutionLoop:
    """
    自我进化主循环：生成 → 验证 → 执行 → 重试 → 汇总结果
    
    用法：
        loop = SelfEvolutionLoop()
        result = loop.run(user_request="帮我写一个快速排序")
        print(result)
    """

    def __init__(self):
        self.executor = CodeExecutor()
        self._generation_history: list[dict] = []

    def run(self, user_request: str, generated_content: str) -> str:
        """
        验证并执行生成的内容。
        
        Args:
            user_request: 用户原始请求（用于判断意图）
            generated_content: LLM 生成的内容（可能是代码，也可能是文本）
        
        Returns:
            执行结果（字符串），失败时返回失败原因
        """
        output_type = detect_output_type(user_request)
        
        # ── 情况1：文本/对话类 → 直接返回 ──
        if output_type == "text":
            return generated_content
        
        # ── 情况2：代码类 → 提取并执行 ──
        code_blocks = extract_code_blocks(generated_content)
        
        if not code_blocks:
            # 没有代码块，说明 LLM 返回的是纯文本/对话内容
            # 检查是否包含代码关键词但没被正确提取
            if any(kw in generated_content.lower() for kw in ["def ", "class ", "import ", "function ", "const ", "let "]):
                # 尝试直接执行整个内容
                return self._execute_with_retry(
                    generated_content, user_request,
                    inline_code=True
                )
            return generated_content  # 纯文本，直接返回
        
        # ── 有代码块 → 逐个执行并验证 ──
        all_results = []
        verifier = ResultVerifier(user_request)
        
        for i, block in enumerate(code_blocks):
            lang = block["language"]
            code = block["code"]
            
            # 跳过明显不是可执行代码的语言
            if lang in ("text", "markdown", "output", "log", "json", "yaml", "toml", "html", "css"):
                # HTML 单独处理（有可视化效果）
                if lang == "html":
                    html_result = self.executor.execute(code, "html", user_request)
                    all_results.append(html_result)
                # 其他纯描述性语言直接跳过
                continue
            
            exec_result = self._execute_with_retry(
                code, user_request,
                language=lang,
                verifier=verifier,
            )
            
            if isinstance(exec_result, ExecutionResult):
                all_results.append(exec_result)
            else:
                all_results.append(exec_result)
        
        # ── 汇总结果 ──
        return self._summarize_results(all_results, code_blocks, user_request)

    def _execute_with_retry(
        self, code: str, user_request: str,
        language: str = "python",
        inline_code: bool = False,
        verifier: Optional[ResultVerifier] = None,
    ) -> ExecutionResult:
        """
        执行代码，支持重试。
        
        重试策略：
          1. 首次执行失败 → 将错误信息反馈给 LLM，要求重新生成
          2. 再次执行新代码
          3. 还失败 → 返回详细错误信息（不再重试，避免死循环）
        """
        if verifier is None:
            verifier = ResultVerifier(user_request)
        
        last_error = ""
        
        for attempt in range(verifier.max_attempts + 1):
            # 执行代码
            if inline_code:
                # 整段内容当作代码执行
                exec_result = self.executor.execute(code, language, user_request)
            else:
                exec_result = self.executor.execute(code, language, user_request)
            
            # 验证结果
            verification = verifier.verify(exec_result)
            
            if verification.is_satisfactory:
                # 验证通过，记录历史
                self._generation_history.append({
                    "user_request": user_request,
                    "code": code,
                    "language": language,
                    "result": exec_result,
                    "attempt": attempt + 1,
                    "success": True,
                })
                return exec_result
            
            last_error = f"[尝试 {attempt + 1}] {verification.reason}。{verification.suggestion}"
            
            # 如果还有重试机会，用错误反馈重新生成代码
            if attempt < verifier.max_attempts:
                code = self._regenerate_with_feedback(
                    original_request=user_request,
                    previous_code=code,
                    error_info=f"{verification.reason}。{verification.suggestion}",
                    language=language,
                )
                if code is None:
                    break  # 重新生成失败，放弃重试
        
        # 所有重试都失败了
        self._generation_history.append({
            "user_request": user_request,
            "code": code,
            "language": language,
            "error": last_error,
            "attempts": verifier.max_attempts + 1,
            "success": False,
        })
        
        return ExecutionResult(
            stdout="",
            stderr=last_error,
            exit_code=1,
        )

    def _regenerate_with_feedback(
        self,
        original_request: str,
        previous_code: str,
        error_info: str,
        language: str,
    ) -> Optional[str]:
        """
        将错误信息反馈给 LLM，重新生成代码。
        """
        try:
            from hongjun.gateway.server import _get_llm
            llm = _get_llm()
            if not llm:
                return None
            
            regen_resp = llm.chat_sync([
                {
                    "role": "system",
                    "content": f"""你是一个专业的代码修复助手。
用户请求：{original_request}

上次生成的代码有错误：
{error_info}

请修复上述问题，重新生成代码。
要求：
1. 直接输出修复后的代码（用 ```{language} ``` 包裹）
2. 不要解释，只给代码
3. 确保代码可以正常运行"""
                },
                {
                    "role": "assistant",
                    "content": f"```{language}\n{previous_code}\n```",
                },
                {
                    "role": "user",
                    "content": f"错误：{error_info}\n\n请重新生成：",
                },
            ])
            
            content = regen_resp.content if hasattr(regen_resp, "content") else str(regen_resp)
            blocks = extract_code_blocks(content)
            if blocks:
                return blocks[0]["code"]
            # 没有代码块 → 尝试提取
            if "```" in content:
                m = re.search(r"```\w*\n(.+?)```", content, re.DOTALL)
                if m:
                    return m.group(1)
            return None
        except Exception:
            return None

    def _summarize_results(
        self,
        results: list[ExecutionResult],
        code_blocks: list[dict],
        user_request: str,
    ) -> str:
        """
        汇总所有执行结果，生成最终返回文本。
        """
        if not results:
            return "（无执行结果）"
        
        parts = []
        has_failure = False
        
        for i, res in enumerate(results):
            block = code_blocks[i] if i < len(code_blocks) else {"language": "unknown", "code": ""}
            lang = block.get("language", "code")
            
            if res.exit_code == 0:
                # 成功：显示输出
                output_preview = res.stdout.strip()
                if len(output_preview) > 2000:
                    output_preview = output_preview[:2000] + f"\n... (共 {len(res.stdout)} 字符)"
                parts.append(f"✅ [{lang.upper()}] 执行成功：\n{output_preview}")
            else:
                # 失败：显示错误
                has_failure = True
                error_msg = (res.stderr or res.stdout or "未知错误").strip()
                if len(error_msg) > 500:
                    error_msg = error_msg[:500] + "\n..."
                parts.append(f"❌ [{lang.upper()}] 执行失败：\n{error_msg}")
        
        summary = "\n\n".join(parts)
        
        if has_failure:
            summary += "\n\n⚠️ 部分代码执行失败，请检查输出内容。"
        
        return summary


# ── 便捷入口函数 ─────────────────────────────────────────────────

def verify_and_execute(user_request: str, llm_response: str) -> str:
    """
    给 orchestrator 调用的便捷入口。
    
    Args:
        user_request: 用户原始请求
        llm_response: LLM 返回的内容（可能是代码或文本）
    
    Returns:
        处理/执行后的最终结果
    """
    loop = SelfEvolutionLoop()
    return loop.run(user_request, llm_response)
