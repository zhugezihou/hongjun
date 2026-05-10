"""
兵部 · 安全护栏
================

基于 NeMo Guardrails 的多层安全防护。

三层防护：
  1. 输入护栏：用户请求进入 Agent 前的安全过滤
  2. 输出护栏：Agent 返回结果前的安全审核
  3. 主题护栏：禁止讨论特定敏感话题

用法：
  security = HongjunSecurity()

  # 输入审核
  passed, error = security.check_input("帮我查一下")
  if not passed:
      raise PermissionError(error)

  # 输出审核
  safe_output = security.check_output(raw_output)
"""

import os
from typing import Tuple, Optional


class HongjunSecurity:
    """
    鸿钧安全护栏

    目前支持两种模式：
    1. NeMo Guardrails（需要 nemoguardrails 已安装）
    2. 轻量级规则引擎（内置，无需额外依赖）
    """

    def __init__(self, use_nemoguardrails: bool = False):
        self.use_nemoguardrails = use_nemoguardrails and self._check_nemo()
        self._rails = None

        if self.use_nemoguardrails:
            self._init_nemo_rails()

    def _check_nemo(self) -> bool:
        """检查 NeMo Guardrails 是否可用"""
        try:
            import nemoguardrails
            return True
        except ImportError:
            return False

    def _init_nemo_rails(self):
        """初始化 NeMo Guardrails"""
        try:
            config_path = os.path.join(
                os.path.dirname(__file__),
                "../../../config/guardrails"
            )
            # 如果没有配置文件，使用简单配置
            self._rails = None  # 暂时禁用，需要配置文件
        except Exception:
            self._rails = None

    # === 危险指令词库 ===
    DANGEROUS_PATTERNS = [
        "rm -rf /",
        "rm -rf /*",
        "drop table",
        "delete from",
        "format c:",
        "shutdown",
        "halt",
        "mkfs",
        ":(){ :|:& };:",  # Fork bomb
        "eval\s*\(",
        "exec\s*\(",
    ]

    # === 敏感话题 ===
    BLOCKED_TOPICS = [
        "如何制作炸弹",
        "如何制造毒品",
        "如何入侵别人电脑",
        "怎么偷钱",
    ]

    def check_input(self, prompt: str) -> Tuple[bool, Optional[str]]:
        """
        输入安全审核

        Args:
            prompt: 用户输入

        Returns:
            (是否通过, 错误信息或 None)
        """
        if self.use_nemoguardrails and self._rails:
            try:
                safe = self._rails.generate(prompt=prompt)
                return True, None
            except Exception as e:
                return False, f"NeMo Guardrails 错误: {e}"

        # === 轻量级规则引擎 ===
        prompt_lower = prompt.lower()

        # 1. 检查危险指令
        import re
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, prompt_lower):
                return False, f"❌ 兵部拦截：检测到危险指令模式 [{pattern}]"

        # 2. 检查敏感话题
        for topic in self.BLOCKED_TOPICS:
            if topic in prompt_lower:
                return False, f"❌ 兵部拦截：话题 [{topic}] 被禁止"

        # 3. 检查 token 长度（防止资源耗尽攻击）
        if len(prompt) > 50_000:
            return False, "❌ 兵部拦截：输入过长（>50k字符）"

        return True, None

    def check_output(self, output: str) -> Tuple[bool, Optional[str]]:
        """
        输出安全审核

        Args:
            output: Agent 生成的输出

        Returns:
            (是否安全, 过滤后内容或错误信息)
        """
        if not output:
            return True, None

        # 1. 检查是否包含敏感信息模式（简单检查）
        sensitive_patterns = [
            r"\b\d{16}\b",  # 信用卡号
            r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
            r"api[_-]?key['\"]?\s*[:=]\s*['\"]?\w+",  # API key
        ]

        import re
        for pattern in sensitive_patterns:
            if re.search(pattern, output):
                # 脱敏处理
                output = re.sub(pattern, "[已脱敏]", output)

        # 2. 检查输出长度
        if len(output) > 100_000:
            output = output[:100_000] + "\n...（输出过长已截断）"

        return True, output

    def check_code(self, code: str) -> Tuple[bool, Optional[str]]:
        """
        代码安全审核

        Args:
            code: 生成的代码

        Returns:
            (是否安全, 错误或警告信息)
        """
        warnings = []
        code_lower = code.lower()

        # 危险函数检查
        dangerous_funcs = [
            ("os.system", "os.system 可能执行任意命令"),
            ("subprocess.call", "subprocess.call 注意参数来源"),
            ("eval(", "eval 可能执行任意代码"),
            ("exec(", "exec 可能执行任意代码"),
            ("__import__", "__import__ 动态导入注意安全"),
            ("requests.get", "requests.get 注意 URL 来源验证"),
        ]

        for func, reason in dangerous_funcs:
            if func in code_lower:
                warnings.append(f"⚠️ [{func}] {reason}")

        if warnings:
            return True, "\n".join(warnings)  # 有警告但允许执行
        return True, None


# === 权限管控 ===

class Permission:
    """权限级别定义"""
    GUEST = 0     # 仅提问，无执行权限
    USER = 1      # 正常提问 + 工具使用
    ADMIN = 2     # 管理员，可执行危险操作
    SYSTEM = 3    # 系统级，不受限制


class PermissionGuard:
    """
    权限守卫

    控制不同用户可以执行的操作范围。
    """

    def __init__(self, user_level: int = Permission.USER):
        self.user_level = user_level

    def can_execute_tool(self, tool_name: str) -> bool:
        """检查是否可以执行指定工具"""
        if self.user_level >= Permission.ADMIN:
            return True

        # Shell 执行仅限 ADMIN+
        if tool_name == "shell":
            return self.user_level >= Permission.ADMIN

        return True

    def can_read_file(self, path: str) -> bool:
        """检查是否可以读取指定路径"""
        if self.user_level >= Permission.ADMIN:
            return True

        # 禁止读取敏感路径
        blocked = ["/etc/", "/root/", "/home/", "/.ssh/"]
        for blocked_path in blocked:
            if path.startswith(blocked_path):
                return self.user_level >= Permission.ADMIN

        return True

    def can_write_file(self, path: str) -> bool:
        """检查是否可以写入指定路径"""
        if self.user_level >= Permission.ADMIN:
            return True

        # 禁止写入系统路径
        system_paths = ["/etc/", "/bin/", "/usr/bin/", "/sbin/"]
        for sp in system_paths:
            if path.startswith(sp):
                return False

        return True
