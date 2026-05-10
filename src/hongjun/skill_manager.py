"""
工部 · Skills 外接系统
======================

鸿钧的插件化能力扩展层。

目录结构：
  hongjun/skills/
    web-scraper/
      SKILL.md          ← skill 定义（YAML frontmatter + 说明）
      scraper.py        ← 可选：Python 实现（若需要复杂逻辑）
    github-ops/
      SKILL.md
      github.py

SKILL.md frontmatter 格式：
  ---
  name: skill-name
  description: |
    skill 功能描述（多行，支持 LLM 理解）
  triggers:
    - "关键词1"
    - "关键词2"
  category: web
  version: "1.0"
  author: "鸿钧·XXX"
  dependencies:        # 可选：pip 包列表
    - "pip install xxx"
  tools:              # 可选：需要的基础工具
    - shell
    - file_read
  ---

用法：
  manager = SkillManager(skills_root="/home/asus/hongjun/skills")
  manager.discover()                    # 启动时扫描所有 skills
  skill = manager.match("抓取网页内容")   # 根据触发词匹配 skill
  result = manager.execute(skill, url="...")  # 执行 skill
"""

from __future__ import annotations

import os
import re
import importlib.util
import subprocess
from datetime import datetime, timezone
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass, field
from pathlib import Path
import yaml

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.skill")


# === 数据类 ===

@dataclass
class Skill:
    """
    Skill 定义

    来自 SKILL.md 的 YAML frontmatter + 解析出的执行函数。
    """
    name: str                          # 唯一标识（如 "web-scraper"）
    description: str                    # 功能描述（供 LLM 理解何时调用）
    triggers: List[str]                 # 触发关键词列表
    category: str = "general"           # 分类：web / devops / data / ...
    version: str = "1.0"
    author: str = "unknown"
    dependencies: List[str] = field(default_factory=list)  # pip 包依赖
    required_tools: List[str] = field(default_factory=list)  # 依赖的基础工具

    root_dir: Path = None               # 所在目录（用于找同目录 .py 文件）

    # === 闭环统计 ===
    usage_count: int = 0                # 总执行次数
    success_count: int = 0              # 成功次数
    confidence: float = 0.5             # 置信度 0.0-1.0（初始 0.5）
    last_used: Optional[str] = None     # ISO 时间戳
    learnings_dir: Path = None          # .learnings/ 目录（用于持久化错误和模式）

    # 动态属性：加载同目录 .py 中的函数
    functions: Dict[str, Callable] = field(default_factory=dict)

    def match_score(self, query: str) -> float:
        """
        计算 query 与这个 skill 的匹配度。

        策略：
        - 精确匹配触发词：+1.0
        - query 包含触发词：+0.7
        - 触发词包含 query：+0.5
        - 关键词重叠（任意方向）：+0.4
        - description 关键词命中：+0.1（有触发词基础时上限更高）
        - category 命中（query 含分类词）：+0.1
        - 成功率加权：usage_count ≥ 3 时，结果乘以 sqrt(success/usage)
        """
        if not query:
            return 0.0

        query_lower = query.lower()
        score = 0.0
        has_trigger_hit = False
        query_words = set(query_lower.replace('_', ' ').split())

        # 触发词匹配（权重最高）
        for trigger in self.triggers:
            trigger_lower = trigger.lower().strip('" ')
            if not trigger_lower:
                continue
            # 双向包含检查：触发词和查询互相包含都算
            if trigger_lower == query_lower:
                score = max(score, 1.0)
                has_trigger_hit = True
            elif trigger_lower in query_lower:
                score = max(score, 0.7)
                has_trigger_hit = True
            elif query_lower in trigger_lower:
                score = max(score, 0.5)
                has_trigger_hit = True
            else:
                # 关键词重叠检查：仅在上层条件都不满足时检查（防止与 substring match 叠加）
                trigger_words = set(trigger_lower.replace('_', ' ').split())
                overlap = trigger_words & query_words
                if overlap and max(len(w) for w in overlap) >= 2:
                    score = max(score, 0.4)
                    has_trigger_hit = True

        # description 关键词匹配
        desc_lower = self.description.lower()
        words = re.findall(r'\w+', query_lower)
        matched_desc = any(len(w) > 2 and w in desc_lower for w in words)
        if matched_desc:
            score = min(score + 0.1, 1.0) if has_trigger_hit else max(score, 0.1)

        # category 加分：query 词中有分类词（而非 category 词在 query 中）
        category_words = set(self.category.lower().replace('_', ' ').split())
        if category_words & query_words:  # query 含分类词
            score = min(score + 0.1, 1.0)

        # 成功率加权：usage_count ≥ 3 才启用（防止冷启动偏差）
        if self.usage_count >= 3 and self.success_count >= 0:
            success_rate = self.success_count / self.usage_count
            # 用 sqrt 平滑：0 success_rate → 0.7倍，1.0 → 1.0倍
            confidence_boost = 0.7 + 0.3 * success_rate
            score *= confidence_boost

        return min(score, 1.0)  # 上限 1.0


# === Skill 管理器 ===

class SkillManager:
    """
    礼部尚书 · Skills 外接系统

    职责：
    1. 发现（discover）：扫描 skills/ 目录，加载所有 SKILL.md
    2. 匹配（match）：根据用户请求，找到最合适的 skill
    3. 执行（execute）：调用 skill 的函数，返回结果
    4. 注册（register_tools）：将 skill 函数注册到 ToolRegistry
    5. 检查（check_dependencies）：检查 pip 依赖是否满足

    用法：
        manager = SkillManager()
        manager.discover()
        skill = manager.match("抓取 example.com 的内容")
        if skill:
            result = manager.execute(skill, url="https://example.com")
    """

    # 默认扫描路径：只扫描鸿钧自己的 skills 目录（独立 Agent 原则）
    # 鸿钧部署到任何机器时，skills/ 目录随源码一起分发
    DEFAULT_ROOTS = [
        Path("/home/asus/hongjun/skills"),
    ]

    def __init__(
        self,
        skills_roots: List[str] = None,
    ):
        if skills_roots:
            self.skills_roots = [Path(p) for p in skills_roots]
        else:
            self.skills_roots = self.DEFAULT_ROOTS
        self.skills: Dict[str, Skill] = {}  # name -> Skill
        self._discovered = False

    # ---- 发现 ----

    def discover(self) -> List[Skill]:
        """
        扫描多个 skills_root 目录，加载所有 skill。

        遍历所有包含 SKILL.md 的子目录，解析 frontmatter，
        并加载同名 .py 文件中的函数。
        """
        discovered = []

        for skills_root in self.skills_roots:
            if not skills_root.exists():
                logger.warning("skill_dir_not_found", path=str(skills_root))
                continue

            for skill_dir in skills_root.iterdir():
                if not skill_dir.is_dir():
                    continue

                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue

                skill = self._load_skill(skill_dir, skill_md)
                if skill:
                    self.skills[skill.name] = skill
                    discovered.append(skill)
                    logger.info("skill_loaded", name=skill.name, version=skill.version, category=skill.category)

        self._discovered = True
        logger.info("skill_discovery_complete", count=len(discovered))
        return discovered

    def _load_skill(self, skill_dir: Path, skill_md: Path) -> Optional[Skill]:
        """解析单个 SKILL.md 文件"""
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("skill_md_read_failed", path=str(skill_md), error=str(e))
            return None

        # 解析 YAML frontmatter
        skill_meta = self._parse_frontmatter(content)
        if not skill_meta:
            logger.warning("skill_skipped_no_frontmatter", dir=skill_dir.name)
            return None

        # 构建 Skill 对象
        skill = Skill(
            name=skill_meta.get("name", skill_dir.name),
            description=skill_meta.get("description", ""),
            triggers=skill_meta.get("triggers", []),
            category=skill_meta.get("category", "general"),
            version=skill_meta.get("version", "1.0"),
            author=skill_meta.get("author", "unknown"),
            dependencies=skill_meta.get("dependencies", []),
            required_tools=skill_meta.get("tools", []),
            root_dir=skill_dir,
        )

        # 加载同名 .py 文件（可选）
        # 尝试 skill_dir/skill_name.py（支持连字符转下划线）
        # 例如 github-ops 目录 → github_ops.py
        def _normalize(name: str) -> str:
            """skill 目录名转 Python 文件名：连字符→下划线"""
            return name.replace("-", "_")

        for py_name in [
            f"{skill.name}.py",                        # github-ops.py
            f"{_normalize(skill.name)}.py",            # github_ops.py
            f"{skill_dir.name}.py",                    # github-ops.py（目录名）
            f"{_normalize(skill_dir.name)}.py",         # github_ops.py
        ]:
            skill_py = skill_dir / py_name
            if skill_py.exists():
                self._load_skill_functions(skill, skill_py)
                break

        return skill

    def _parse_frontmatter(self, content: str) -> Optional[Dict]:
        """解析 YAML frontmatter（--- ... --- 之间的内容）"""
        match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if not match:
            return None
        try:
            return yaml.safe_load(match.group(1))
        except yaml.YAMLError as e:
            logger.warning("skill_yaml_parse_error", error=str(e))
            return None

    def _load_skill_functions(self, skill: Skill, py_file: Path):
        """动态加载 skill 目录下的 Python 函数"""
        try:
            spec = importlib.util.spec_from_file_location("skill_module", py_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # 收集所有 callable（排除私有、以 _ 开头、和 Python 内置类型）
                for attr_name in dir(module):
                    if attr_name.startswith("_"):
                        continue
                    # 过滤 Python 内置类型、类型提示和标准库模块
                    if attr_name in ("Optional", "List", "Dict", "Any", "Callable",
                                     "Union", "Literal", "TypedDict",
                                     "date", "timedelta", "os", "re", "json",
                                     "subprocess", "urllib", "Path",
                                     "BeautifulSoup", "html"):
                        continue
                    attr = getattr(module, attr_name)
                    if callable(attr):
                        skill.functions[attr_name] = attr

                if skill.functions:
                    logger.debug("skill_functions_loaded", skill=skill.name, functions=list(skill.functions.keys()))
        except Exception as e:
            logger.warning("skill_py_load_failed", skill=skill.name, path=str(py_file), error=str(e))

    # ---- 匹配 ----

    def match(self, query: str, top_k: int = 3) -> List[Skill]:
        """
        根据 query 匹配最相关的 skills。

        Args:
            query: 用户请求（自然语言）
            top_k: 返回最多 top_k 个匹配结果

        Returns:
            按匹配度降序排列的 Skill 列表
        """
        if not self._discovered:
            self.discover()

        scored = []
        for skill in self.skills.values():
            score = skill.match_score(query)
            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:top_k]]

    def get_skill(self, name: str) -> Optional[Skill]:
        """按名称获取 skill"""
        return self.skills.get(name)

    # ---- 执行 ----

    def execute(
        self,
        skill: Skill,
        function_name: str = None,
        **kwargs,
    ) -> str:
        """
        执行 skill 的函数。

        Args:
            skill: Skill 对象
            function_name: 要执行的函数名（默认使用 functions 中的第一个）
            **kwargs: 传递给函数的参数

        Returns:
            执行结果的字符串描述
        """
        if not skill.functions:
            return f"⚠️ Skill '{skill.name}' 无可执行函数"

        # 选择函数
        if function_name and function_name in skill.functions:
            func = skill.functions[function_name]
        else:
            # 默认使用第一个
            func_name, func = next(iter(skill.functions.items()))
            function_name = func_name

        # 调用
        now = datetime.now(timezone.utc).isoformat()
        skill.usage_count += 1
        skill.last_used = now

        try:
            result = func(**kwargs)
            skill.success_count += 1

            # 成功率 → confidence 平滑更新（指数移动平均）
            success_rate = skill.success_count / skill.usage_count
            skill.confidence = 0.3 * skill.confidence + 0.7 * success_rate

            # on_skill_complete hook
            self._emit_learnings(skill, "success", {"function": function_name, "kwargs": kwargs})

            # 标准化为字符串
            if isinstance(result, str):
                return result
            elif result is None:
                return f"✅ {skill.name}.{function_name} 执行完成"
            else:
                return str(result)
        except TypeError as e:
            self._emit_learnings(skill, "type_error", {"function": function_name, "kwargs": kwargs, "error": str(e)})
            # 参数不匹配，返回 usage 信息
            return f"⚠️ 参数错误: {e}\n用法: {skill.name}.{function_name}({', '.join(kwargs.keys())})"
        except Exception as e:
            self._emit_learnings(skill, "error", {"function": function_name, "kwargs": kwargs, "error": str(e)})
            return f"❌ 执行失败: {e}"

    # ---- 工具注册 ----

    def register_to_registry(self, registry):
        """
        将所有 skill 函数注册到 ToolRegistry。

        注册后的 tool 名称格式：skill_<name>__<func>
        例如：skill_web-scraper__scrape

        Args:
            registry: ToolRegistry 实例
        """
        for skill in self.skills.values():
            for func_name, func in skill.functions.items():
                tool_name = f"skill_{skill.name}__{func_name}"
                registry.register(
                    name=tool_name,
                    func=func,
                    description=f"[{skill.category}] {skill.description[:100]}",
                    parameters={"skill": skill.name, "function": func_name},
                )
                logger.info("skill_tool_registered", tool=tool_name, category=skill.category)

    # ---- 依赖检查 ----

    def check_dependencies(self, skill: Skill) -> List[str]:
        """
        检查 skill 的 pip 依赖是否满足。

        Returns:
            未满足的依赖列表（空 = 全部满足）
        """
        missing = []
        for dep in skill.dependencies:
            # 解析 "pip install xxx" 或 "xxx"
            pkg = dep.replace("pip install ", "").strip()
            if not self._is_package_available(pkg):
                missing.append(dep)
        return missing

    def _is_package_available(self, pkg: str) -> bool:
        """检查 Python 包是否已安装"""
        import importlib
        name = pkg.split("==")[0].split(">=")[0].split("<=")[0].strip()
        try:
            importlib.import_module(name)
            return True
        except ImportError:
            return False

    def install_dependencies(self, skill: Skill) -> str:
        """为 skill 安装缺失的依赖"""
        missing = self.check_dependencies(skill)
        if not missing:
            return f"✅ {skill.name} 依赖已满足"

        results = []
        for dep in missing:
            try:
                subprocess.run(
                    ["pip", "install"] + [dep.replace("pip install ", "")],
                    check=True,
                    capture_output=True,
                )
                results.append(f"✅ 安装成功: {dep}")
            except Exception as e:
                results.append(f"❌ 安装失败: {dep} — {e}")

        return "\n".join(results)

    # ---- Learnings 持久化 ----

    def _get_learnings_dir(self, skill: Skill) -> Path:
        """确保 skill 的 .learnings/ 目录存在，返回路径"""
        if skill.learnings_dir is None:
            if skill.root_dir:
                skill.learnings_dir = skill.root_dir / ".learnings"
            else:
                skill.learnings_dir = Path(f"/tmp/hongjun-skills/.learnings/{skill.name}")
        skill.learnings_dir.mkdir(parents=True, exist_ok=True)
        return skill.learnings_dir

    def _emit_learnings(self, skill: Skill, event_type: str, context: dict) -> None:
        """
        写入一次执行记录到 .learnings/。

        输出：
        - ERRORS.md   — 记录 error/type_error，包含错误信息、函数、时间戳
        - PATTERNS.md — 记录 success，反映成功模式（可扩展）
        """
        try:
            ld = self._get_learnings_dir(skill)
            now = datetime.now(timezone.utc).isoformat()
            func_name = context.get("function", "?")
            error_msg = context.get("error", "")

            if event_type in ("error", "type_error"):
                # 追加到 ERRORS.md
                err_file = ld / "ERRORS.md"
                entry = (
                    f"## [{now}] {event_type} @ {skill.name}.{func_name}\n"
                    f"- **Error**: {error_msg}\n"
                    f"- **Kwargs**: {context.get('kwargs', {})}\n"
                    f"- **Confidence**: {skill.confidence:.3f} "
                    f"(success_rate={skill.success_count}/{skill.usage_count})\n\n"
                )
                with open(err_file, "a", encoding="utf-8") as f:
                    f.write(entry)

                logger.debug(
                    "skill_error_logged",
                    skill=skill.name,
                    event=event_type,
                    error=error_msg,
                    confidence=skill.confidence,
                )

            elif event_type == "success":
                # 追加到 PATTERNS.md（简化版：只记录成功事实）
                pat_file = ld / "PATTERNS.md"
                entry = (
                    f"## [{now}] success @ {skill.name}.{func_name}\n"
                    f"- **Kwargs**: {context.get('kwargs', {})}\n"
                    f"- **Confidence**: {skill.confidence:.3f} "
                    f"(success_rate={skill.success_count}/{skill.usage_count})\n\n"
                )
                with open(pat_file, "a", encoding="utf-8") as f:
                    f.write(entry)

        except Exception as e:
            logger.warning("skill_learnings_write_failed", skill=skill.name, error=str(e))

    def get_learnings_summary(self, skill: Skill) -> dict:
        """读取 skill 的 learnings 摘要（用于调试/分析）"""
        ld = self._get_learnings_dir(skill)
        summary = {"errors": [], "patterns": []}
        err_file = ld / "ERRORS.md"
        pat_file = ld / "PATTERNS.md"
        if err_file.exists():
            summary["errors"] = err_file.read_text(encoding="utf-8").strip().split("\n## ##")
        if pat_file.exists():
            summary["patterns"] = pat_file.read_text(encoding="utf-8").strip().split("\n## ##")
        return summary

    # ---- 列表 ----

    def list_skills(self) -> List[Dict]:
        """返回所有已加载 skill 的概要"""
        return [
            {
                "name": s.name,
                "category": s.category,
                "version": s.version,
                "author": s.author,
                "description": s.description[:60] + "...",
                "triggers": s.triggers,
                "functions": list(s.functions.keys()),
            }
            for s in self.skills.values()
        ]


# === 内置 skill 函数（无 .py 文件时的备选实现）===

def scrape(url: str, selector: str = "article", format: str = "markdown") -> str:
    """
    内置网页抓取函数（不依赖 requests/bs4 的轻量版）。

    使用 httpx + re 实现，适用于简单页面。
    """
    import httpx
    try:
        resp = httpx.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Hongjun/1.0)"
        })
        resp.raise_for_status()
        html = resp.text

        # 简单文本提取：移除 script/style
        import re
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()

        if format == "json":
            import json
            return json.dumps({"url": url, "text": text[:2000]}, ensure_ascii=False)
        return text[:2000] + ("...（已截断）" if len(text) > 2000 else "")

    except httpx.Timeout:
        return f"⏰ 超时（>{10}s）"
    except Exception as e:
        return f"❌ 抓取失败: {e}"


def github_search(query: str, language: str = None, sort: str = "stars") -> str:
    """
    GitHub 仓库搜索（轻量版，curl + gh CLI 或 HTTP）。
    """
    import subprocess
    import os
    import json

    # 优先用 gh CLI
    try:
        cmd = ["gh", "api", "search/repositories",
               "--jq", ".items[] | {name: .full_name, stars: .stargazers_count, desc: .description}"]
        if query:
            cmd[-1] = "-q", f"{query} in:name,description"
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            return f"🔍 GitHub 搜索 '{query}':\n{result.stdout[:1000]}"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 降级：用 curl + GitHub API
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    import urllib.request
    q = urllib.parse.quote(query)
    url = f"https://api.github.com/search/repositories?q={q}&sort={sort}&per_page=5"
    if language:
        url += f"+language:{urllib.parse.quote(language)}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            items = data.get("items", [])
            if not items:
                return f"🔍 未找到 '{query}' 相关仓库"
            lines = [f"🔍 GitHub 搜索 '{query}':"]
            for item in items:
                lines.append(f"  ★ {item['stargazers_count']} | {item['full_name']}")
                if item.get("description"):
                    lines.append(f"    {item['description'][:80]}")
            return "\n".join(lines)
    except Exception as e:
        return f"❌ GitHub 搜索失败: {e}"


# === 全局单例 ===

_env_val = os.environ.get("HONGJUN_SKILLS_ROOTS", "")
_skills_roots = [p for p in _env_val.split(":") if p] or None
SKILL_MANAGER = SkillManager(skills_roots=_skills_roots)
# 启动时立即发现 skills（避免单例初始化后 skills 为空的 bug）
SKILL_MANAGER.discover()


# === 单元测试 ===
if __name__ == "__main__":
    print("=" * 50)
    print("鸿钧 · Skills 系统测试")
    print("=" * 50)

    manager = SkillManager()

    # 发现
    skills = manager.discover()

    # 列表
    print(f"\n已加载 {len(skills)} 个 skills:")
    for s in manager.list_skills():
        print(f"  [{s['category']}] {s['name']} — {s['description']}")

    # 匹配测试
    test_queries = [
        "抓取网页内容",
        "github trending",
        "搜索仓库",
        "帮我查一下这个网页",
    ]
    print("\n匹配测试:")
    for q in test_queries:
        matched = manager.match(q)
        if matched:
            print(f"  '{q}' → {matched[0].name} (score={matched[0].match_score(q):.2f})")
        else:
            print(f"  '{q}' → 无匹配")

    # 内置函数测试
    print("\n内置函数测试:")
    print("  scrape:", scrape("https://example.com")[:100])
    print("  github_search:", github_search("browser-use AI")[:100])
