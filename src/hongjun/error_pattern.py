"""
鸿钧 · 错误模式积累系统
=========================

错误类型 → 修复方案 映射库。

每次任务失败时：
  1. 提取错误类型和关键词
  2. 查找是否有已知修复方案
  3. 如果有 → 自动应用已知修复
  4. 如果没有 → 记录新错误类型，下次遇到时可复用

使用方式：
    ep = ErrorPatternLibrary()
    fix = ep.lookup("ImportError", "No module named 'yaml'")
    if fix:
        ep.apply_fix(fix)   # 自动修复
    else:
        ep.record_new_error("ImportError", "No module named 'yaml'", applied_fix="pip install pyyaml")
    
    # 推荐修复方案
    suggestions = ep.suggest("ModuleNotFoundError", context="feishu")
"""

from __future__ import annotations
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.error_pattern")

PATTERNS_DIR = Path.home() / ".hongjun"
PATTERNS_FILE = PATTERNS_DIR / "error_patterns.json"


# ── 错误模式条目 ────────────────────────────────────────────────────────────

class ErrorPattern:
    """单条错误模式"""

    def __init__(
        self,
        pattern_id: str,
        error_type: str,          # ImportError / RuntimeError / TimeoutError ...
        error_keywords: list[str], # 错误信息中的关键词
        module: str,              # 出错的模块
        fix_description: str,     # 修复方案描述
        fix_command: str = "",    # 修复命令（shell）
        fix_code: str = "",       # 修复代码片段
        success_rate: float = 0,  # 历史成功率
        times_seen: int = 0,      # 遇到次数
        times_fixed: int = 0,     # 修复成功次数
        last_seen: str = "",
        last_fixed: str = "",
        verified: bool = False,   # 是否已验证
    ):
        self.pattern_id = pattern_id
        self.error_type = error_type
        self.error_keywords = error_keywords
        self.module = module
        self.fix_description = fix_description
        self.fix_command = fix_command
        self.fix_code = fix_code
        self.success_rate = success_rate
        self.times_seen = times_seen
        self.times_fixed = times_fixed
        self.last_seen = last_seen
        self.last_fixed = last_fixed
        self.verified = verified

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "error_type": self.error_type,
            "error_keywords": self.error_keywords,
            "module": self.module,
            "fix_description": self.fix_description,
            "fix_command": self.fix_command,
            "fix_code": self.fix_code,
            "success_rate": self.success_rate,
            "times_seen": self.times_seen,
            "times_fixed": self.times_fixed,
            "last_seen": self.last_seen,
            "last_fixed": self.last_fixed,
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ErrorPattern":
        return cls(**{k: v for k, v in d.items() if k in cls.__init__.__code__.co_varnames})

    def bump_seen(self):
        self.times_seen += 1
        self.last_seen = datetime.now().isoformat()
        if self.success_rate < 0:
            self.success_rate = 0

    def bump_fixed(self):
        self.times_fixed += 1
        self.last_fixed = datetime.now().isoformat()
        self.success_rate = self.times_fixed / self.times_seen if self.times_seen > 0 else 1.0
        if self.success_rate >= 0.7:
            self.verified = True


# ── 错误模式库 ──────────────────────────────────────────────────────────────

class ErrorPatternLibrary:
    """
    错误模式积累库。

    使用方式：
        lib = ErrorPatternLibrary()
        
        # 查找已知修复
        fix = lib.lookup_by_error("ImportError", "No module named 'yaml'")
        if fix:
            print(f"已知修复: {fix.fix_description}")
        
        # 记录新错误
        lib.record("RuntimeError", "maximum recursion", "python",
                   "递归深度超限", fix_command="增加 sys.setrecursionlimit(10000)")
        
        # 推荐修复
        suggestions = lib.suggest("ModuleNotFoundError", context="feishu")
    """

    def __init__(self):
        PATTERNS_DIR.mkdir(parents=True, exist_ok=True)
        self.patterns: dict[str, ErrorPattern] = {}
        self._load()

    # ── 持久化 ──────────────────────────────────────────────────────────

    def _load(self):
        if not PATTERNS_FILE.exists():
            self._init_builtin_patterns()
            return
        try:
            with open(PATTERNS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for p in data.get("patterns", []):
                self.patterns[p["pattern_id"]] = ErrorPattern.from_dict(p)
            logger.info(f"加载了 {len(self.patterns)} 个错误模式")
        except Exception as e:
            logger.warning(f"加载错误模式失败: {e}")
            self._init_builtin_patterns()

    def _save(self):
        try:
            data = {
                "version": 1,
                "updated_at": datetime.now().isoformat(),
                "patterns": [p.to_dict() for p in self.patterns.values()],
            }
            with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存错误模式失败: {e}")

    def _init_builtin_patterns(self):
        """内置常见错误模式（不需要学习就知道的）"""
        builtins = [
            ErrorPattern(
                pattern_id="import_error_yaml",
                error_type="ImportError",
                error_keywords=["yaml", "pyyaml"],
                module="config",
                fix_description="安装 pyyaml",
                fix_command="pip install pyyaml",
                success_rate=1.0,
                times_seen=0,
                times_fixed=0,
                verified=True,
            ),
            ErrorPattern(
                pattern_id="import_error_httpx",
                error_type="ImportError",
                error_keywords=["httpx"],
                module="network",
                fix_description="安装 httpx",
                fix_command="pip install httpx",
                success_rate=1.0,
                times_seen=0,
                times_fixed=0,
                verified=True,
            ),
            ErrorPattern(
                pattern_id="timeout_gateway",
                error_type="TimeoutError",
                error_keywords=["Gateway", "20830", "timed out"],
                module="gateway",
                fix_description="Gateway 超时，重启 Gateway 进程",
                fix_command="systemctl restart hongjun-gateway || (pkill -f gateway && cd /home/asus/hongjun && python -m hongjun.gateway.server &)",
                success_rate=0.9,
                times_seen=0,
                times_fixed=0,
                verified=True,
            ),
            ErrorPattern(
                pattern_id="syntax_error",
                error_type="SyntaxError",
                error_keywords=["SyntaxError", "invalid syntax"],
                module="code",
                fix_description="语法错误，修正代码语法",
                success_rate=0.8,
                times_seen=0,
                times_fixed=0,
                verified=False,
            ),
            ErrorPattern(
                pattern_id="git_push_rejected",
                error_type="GitError",
                error_keywords=["push", "rejected", "non-fast-forward"],
                module="git",
                fix_description="Git push 被拒绝，先 pull rebase 再 push",
                fix_command="git pull --rebase origin main && git push origin main",
                success_rate=0.95,
                times_seen=0,
                times_fixed=0,
                verified=True,
            ),
            ErrorPattern(
                pattern_id="module_not_found",
                error_type="ModuleNotFoundError",
                error_keywords=["No module named", "ModuleNotFoundError"],
                module="python",
                fix_description="缺少 Python 模块，使用 pip install 安装",
                fix_command="",  # 需要从错误信息中提取模块名
                success_rate=1.0,
                times_seen=0,
                times_fixed=0,
                verified=True,
            ),
            ErrorPattern(
                pattern_id="file_not_found",
                error_type="FileNotFoundError",
                error_keywords=["No such file", "FileNotFoundError", "不存在"],
                module="filesystem",
                fix_description="文件不存在，检查路径是否正确",
                success_rate=0.9,
                times_seen=0,
                times_fixed=0,
                verified=True,
            ),
            ErrorPattern(
                pattern_id="memory_error",
                error_type="MemoryError",
                error_keywords=["MemoryError", "out of memory"],
                module="system",
                fix_description="内存不足，减少批处理大小或清理内存",
                success_rate=0.5,
                times_seen=0,
                times_fixed=0,
                verified=False,
            ),
        ]
        for p in builtins:
            self.patterns[p.pattern_id] = p
        self._save()
        logger.info(f"初始化了 {len(builtins)} 个内置错误模式")

    # ── 查询 ────────────────────────────────────────────────────────────

    def lookup_by_error(self, error_type: str, error_message: str) -> Optional[ErrorPattern]:
        """
        根据错误类型和错误信息查找已知修复方案。

        Returns:
            最匹配的 ErrorPattern 或 None
        """
        msg_lower = error_message.lower()

        candidates = []
        for p in self.patterns.values():
            # 错误类型匹配
            if p.error_type and p.error_type != error_type:
                continue
            # 关键词匹配
            score = sum(1 for kw in p.error_keywords if kw.lower() in msg_lower)
            if score > 0:
                candidates.append((score, p))

        if not candidates:
            return None

        # 返回得分最高且历史成功率最高的
        candidates.sort(key=lambda x: (x[0], x[1].success_rate), reverse=True)
        return candidates[0][1]

    def suggest(self, error_type: str, context: str = "", limit: int = 3) -> list[ErrorPattern]:
        """
        根据错误类型和上下文推荐可能的修复方案。
        """
        candidates = []
        ctx_lower = context.lower()

        for p in self.patterns.values():
            if p.error_type == error_type or not error_type:
                # 上下文相关性
                if context:
                    score = sum(1 for kw in p.error_keywords if kw in ctx_lower)
                else:
                    score = p.success_rate
                candidates.append((score, p))

        candidates.sort(key=lambda x: (x[0], x[1].success_rate), reverse=True)
        return [p for _, p in candidates[:limit]]

    def record(
        self,
        error_type: str,
        error_message: str,
        module: str,
        fix_description: str = "",
        fix_command: str = "",
        fix_code: str = "",
    ) -> ErrorPattern:
        """
        记录一个新错误模式。

        Returns:
            新建的 ErrorPattern 条目
        """
        # 提取关键词
        keywords = self._extract_keywords(error_type, error_message)

        # 检查是否已存在类似模式
        existing = self._find_similar(keywords)
        if existing:
            existing.bump_seen()
            if fix_description:
                existing.fix_description = fix_description
            if fix_command:
                existing.fix_command = fix_command
            self._save()
            logger.info(f"已有类似模式 [{existing.pattern_id}]，更新计数")
            return existing

        # 新建
        pid = f"{error_type.lower()}_{int(time.time() * 1000)}"
        pid = re.sub(r'[^a-z0-9_]', '', pid)[:60]

        pattern = ErrorPattern(
            pattern_id=pid,
            error_type=error_type,
            error_keywords=keywords,
            module=module,
            fix_description=fix_description,
            fix_command=fix_command,
            fix_code=fix_code,
            success_rate=0,
            times_seen=1,
            times_fixed=0,
            last_seen=datetime.now().isoformat(),
            verified=False,
        )
        self.patterns[pid] = pattern
        self._save()
        logger.info(f"新错误模式 [{pid}]: {error_type} @ {module}")
        return pattern

    def record_fix_success(self, pattern_id: str):
        """记录某错误模式修复成功"""
        if pattern_id in self.patterns:
            self.patterns[pattern_id].bump_fixed()
            self._save()
            logger.info(f"✅ 修复成功 [{pattern_id}]: 成功率 {self.patterns[pattern_id].success_rate:.0%}")

    def record_fix_failure(self, pattern_id: str):
        """记录某错误模式修复失败"""
        if pattern_id in self.patterns:
            p = self.patterns[pattern_id]
            p.success_rate = p.times_fixed / p.times_seen if p.times_seen > 0 else 0
            self._save()

    # ── 工具 ────────────────────────────────────────────────────────────

    def _extract_keywords(self, error_type: str, error_message: str) -> list[str]:
        """从错误信息中提取有意义的关键词"""
        # 去除常见无意义词
        stopwords = {"the", "a", "an", "is", "was", "were", "error", "failed", "cannot", "could"}
        words = re.findall(r'[A-Za-z_][A-Za-z0-9_.]*', error_message)
        meaningful = [w for w in words if w.lower() not in stopwords and len(w) > 2]
        # 去重保序
        seen = set()
        unique = []
        for w in meaningful:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        return unique[:10]  # 最多10个关键词

    def _find_similar(self, keywords: list[str]) -> Optional[ErrorPattern]:
        """找相似已有模式（关键词重合>=2）"""
        for p in self.patterns.values():
            overlap = sum(1 for kw in keywords if kw in p.error_keywords)
            if overlap >= 2:
                return p
        return None

    def get_stats(self) -> dict:
        """获取错误模式统计"""
        total = len(self.patterns)
        verified = sum(1 for p in self.patterns.values() if p.verified)
        high_success = sum(1 for p in self.patterns.values() if p.success_rate >= 0.7)
        most_seen = sorted(self.patterns.values(), key=lambda p: p.times_seen, reverse=True)[:5]
        return {
            "total": total,
            "verified": verified,
            "high_success": high_success,
            "top_patterns": [
                {"id": p.pattern_id, "error_type": p.error_type, "times_seen": p.times_seen,
                 "success_rate": p.success_rate}
                for p in most_seen
            ],
        }


# ── 全局单例 ────────────────────────────────────────────────────────────────

_library: Optional[ErrorPatternLibrary] = None


def get_error_library() -> ErrorPatternLibrary:
    global _library
    if _library is None:
        _library = ErrorPatternLibrary()
    return _library
