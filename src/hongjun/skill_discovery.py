"""
鸿钧 · 主动技能发现系统
=========================

定期搜索 GitHub trending 和相关资源，发现可扩展鸿钧能力的新工具/项目。

发现流程：
  1. 每天扫描 GitHub trending（Python / JavaScript）
  2. 搜索关键词：AI agent / tool use / browser automation / memory system
  3. 评估项目是否适合集成（star数、文档质量、更新频率）
  4. 生成摘要报告，推送给用户（朝堂群）
  5. 用户确认后可自动研究并尝试集成

使用方式：
    disco = SkillDiscovery()
    findings = disco.discover(force_refresh=True)  # 强制刷新
    disco.notify_findings(findings)                 # 推送到飞书
"""

from __future__ import annotations
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.skill_discovery")

CACHE_DIR = Path.home() / ".hongjun" / "skill_discovery"
CACHE_FILE = CACHE_DIR / "findings.json"
INTERESTS_FILE = CACHE_DIR / "interests.json"

# 发现者感兴趣的技术方向
DEFAULT_INTERESTS = [
    "AI agent framework",
    "browser automation",
    "web scraping",
    "LLM memory system",
    "LangGraph",
    "Playwright MCP",
    "knowledge graph",
    "RAG system",
    "task automation",
    "self-improving AI",
]


# ── 发现条目 ────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """单条发现"""
    repo: str                  # "owner/repo"
    description: str            # 项目描述
    stars: int                 # star 数
    language: str              # 主要语言
    relevance_score: float     # 相关度评分 0-1
    relevance_reason: str      # 为什么相关
    tech_stack: list[str]     # 技术栈关键词
    discovered_at: str         # 发现时间
    notified: bool = False    # 是否已推送
    evaluated: bool = False    # 是否已评估
    evaluation_note: str = ""  # 评估备注

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "description": self.description,
            "stars": self.stars,
            "language": self.language,
            "relevance_score": self.relevance_score,
            "relevance_reason": self.relevance_reason,
            "tech_stack": self.tech_stack,
            "discovered_at": self.discovered_at,
            "notified": self.notified,
            "evaluated": self.evaluated,
            "evaluation_note": self.evaluation_note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        return cls(**{k: v for k, v in d.items()
                       if k in cls.__dataclass_fields__})


# ── 技能发现引擎 ────────────────────────────────────────────────────────────

class SkillDiscovery:
    """
    主动技能发现。

    使用方式：
        disco = SkillDiscovery()
        findings = disco.discover()              # 发现新项目
        disco.notify_findings(findings)          # 推送飞书
        disco.mark_evaluated(repo, "值得关注")  # 标记已评估
    """

    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.findings: dict[str, Finding] = {}
        self.interests: list[str] = []
        self._load()
        self._load_interests()

    # ── 持久化 ─────────────────────────────────────────────────────────

    def _load(self):
        if not CACHE_FILE.exists():
            return
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for fd in data.get("findings", []):
                self.findings[fd["repo"]] = Finding.from_dict(fd)
            logger.info(f"加载了 {len(self.findings)} 条技能发现")
        except Exception as e:
            logger.warning(f"加载技能发现失败: {e}")

    def _save(self):
        try:
            data = {
                "version": 1,
                "updated_at": datetime.now().isoformat(),
                "findings": [f.to_dict() for f in self.findings.values()],
            }
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存技能发现失败: {e}")

    def _load_interests(self):
        if INTERESTS_FILE.exists():
            try:
                with open(INTERESTS_FILE, "r", encoding="utf-8") as f:
                    self.interests = json.load(f)
                return
            except Exception:
                pass
        self.interests = DEFAULT_INTERESTS

    def update_interests(self, interests: list[str]):
        """更新感兴趣的方向"""
        self.interests = interests
        try:
            with open(INTERESTS_FILE, "w", encoding="utf-8") as f:
                json.dump(interests, f)
        except Exception as e:
            logger.error(f"保存兴趣失败: {e}")

    # ── 发现 ────────────────────────────────────────────────────────────

    def discover(self, force_refresh: bool = False) -> list[Finding]:
        """
        执行一次技能发现扫描。

        扫描范围：
          1. GitHub trending（Python / JavaScript）
          2. 相关关键词搜索

        Args:
            force_refresh: True = 即使有缓存也重新搜索

        Returns:
            新发现的 Finding 列表（按相关性排序）
        """
        logger.info("🔍 开始技能发现扫描...")
        new_findings = []

        # 1. 搜索 GitHub trending
        trending = self._search_trending(limit=30)
        for repo, desc, stars, lang in trending:
            if repo in self.findings:
                continue
            relevance, reason, stack = self._eval_relevance(desc, stars)
            if relevance < 0.3:
                continue

            finding = Finding(
                repo=repo,
                description=desc,
                stars=stars,
                language=lang,
                relevance_score=relevance,
                relevance_reason=reason,
                tech_stack=stack,
                discovered_at=datetime.now().isoformat(),
            )
            self.findings[repo] = finding
            new_findings.append(finding)
            logger.info(f"  ⭐ 发现相关项目: {repo} (stars={stars}, relevance={relevance:.2f})")

        # 2. 关键词搜索（通过 tavily 或直接 GitHub API）
        keyword_results = self._search_by_interests()
        for repo, desc, stars, lang in keyword_results:
            if repo in self.findings or repo in [f.repo for f in new_findings]:
                continue
            relevance, reason, stack = self._eval_relevance(desc, stars)
            if relevance < 0.4:
                continue

            finding = Finding(
                repo=repo,
                description=desc,
                stars=stars,
                language=lang,
                relevance_score=relevance,
                relevance_reason=reason,
                tech_stack=stack,
                discovered_at=datetime.now().isoformat(),
            )
            self.findings[repo] = finding
            new_findings.append(finding)

        self._save()
        logger.info(f"技能发现完成：新发现 {len(new_findings)} 个项目")
        return sorted(new_findings, key=lambda f: f.relevance_score, reverse=True)

    def _search_trending(self, limit: int = 30) -> list[tuple[str, str, int, str]]:
        """搜索 GitHub trending"""
        results = []
        for lang in ["python", "javascript"]:
            try:
                import httpx
                resp = httpx.get(
                    f"https://api.github.com/search/repositories",
                    params={
                        "q": f"stars:>100 pushed:>{datetime.now().date().isoformat()} language:{lang}",
                        "sort": "stars",
                        "order": "desc",
                        "per_page": min(limit, 30),
                    },
                    headers={"Accept": "application/vnd.github.v3+json"},
                    timeout=15,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for item in data.get("items", [])[:limit]:
                    results.append((
                        item["full_name"],
                        item.get("description", "") or "",
                        item.get("stargazers_count", 0),
                        item.get("language", "") or "",
                    ))
            except Exception as e:
                logger.warning(f"GitHub trending 搜索失败: {e}")
        return results

    def _search_by_interests(self) -> list[tuple[str, str, int, str]]:
        """根据感兴趣的方向搜索"""
        results = []
        for interest in self.interests[:5]:  # 最多5个方向
            try:
                import httpx
                resp = httpx.get(
                    "https://api.github.com/search/repositories",
                    params={
                        "q": f"{interest} stars:>500 pushed:>{datetime.now().date().isoformat()}",
                        "sort": "stars",
                        "order": "desc",
                        "per_page": 5,
                    },
                    headers={"Accept": "application/vnd.github.v3+json"},
                    timeout=15,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for item in data.get("items", [])[:5]:
                    results.append((
                        item["full_name"],
                        item.get("description", "") or "",
                        item.get("stargazers_count", 0),
                        item.get("language", "") or "",
                    ))
            except Exception as e:
                logger.warning(f"GitHub 搜索失败 [{interest}]: {e}")
        return results

    def _eval_relevance(
        self, description: str, stars: int
    ) -> tuple[float, str, list[str]]:
        """
        评估项目与鸿钧的相关性。

        Returns:
            (relevance_score, reason, tech_stack)
        """
        desc_lower = description.lower()
        tech_stack = []

        # 技术栈关键词检测
        tech_keywords = {
            "langgraph": ["langgraph", "langchain"],
            "browser": ["playwright", "puppeteer", "selenium", "browser"],
            "mcp": ["mcp", "model context protocol"],
            "memory": ["memory", "mempalace", "knowledge graph", "knowledge-graph"],
            "agent": ["agent", "autonomous", "reAct", "plan-and-execute"],
            "scraping": ["scraping", "scraper", "crawl", "extract"],
            "rag": ["rag", "retrieval", "vector", "embedding"],
            "automation": ["automation", "workflow", "task"],
        }

        for tech, keywords in tech_keywords.items():
            if any(kw in desc_lower for kw in keywords):
                tech_stack.append(tech)

        # 基础相关性评分
        score = 0.0
        reasons = []

        # 兴趣匹配
        for interest in self.interests:
            if interest.lower() in desc_lower:
                score += 0.15
                reasons.append(f"匹配兴趣: {interest}")

        # 技术栈加分
        if tech_stack:
            score += 0.1 * len(tech_stack)
            reasons.append(f"技术栈: {', '.join(tech_stack)}")

        # Star 加分（上限0.15）
        if stars > 10000:
            score += 0.15
        elif stars > 1000:
            score += 0.1
        elif stars > 100:
            score += 0.05

        score = min(score, 1.0)
        reason = "; ".join(reasons) if reasons else "通用相关"

        return score, reason, tech_stack

    # ── 推送 ───────────────────────────────────────────────────────────

    def notify_findings(self, findings: list[Finding]) -> int:
        """推送发现到飞书朝堂群"""
        if not findings:
            return 0

        # 只推送新发现的、未推送过的
        to_notify = [f for f in findings if not f.notified]
        if not to_notify:
            return 0

        lines = ["🔬 **鸿钧技能发现报告**"]
        lines.append(f"扫描方向：{', '.join(self.interests[:5])}")
        lines.append("")

        for f in to_notify[:5]:  # 每次最多推送5个
            icon = "🔥" if f.stars > 5000 else "✨"
            lines.append(f"{icon} **{f.repo}**")
            lines.append(f"   ⭐ {f.stars:,} stars | {f.language or '?'} | 相关度 {f.relevance_score:.0%}")
            if f.description:
                lines.append(f"   📝 {f.description[:100]}")
            if f.tech_stack:
                lines.append(f"   🛠️  技术栈：{', '.join(f.tech_stack)}")
            lines.append(f"   💡 {f.relevance_reason}")
            lines.append("")

        lines.append("---")
        lines.append("回复「研究 [repo]」让鸿钧深入研究该项目")

        message = "\n".join(lines)
        sent = self._send_feishu(message)

        if sent:
            for f in to_notify[:5]:
                f.notified = True
            self._save()

        return len(to_notify[:5])

    def _send_feishu(self, message: str) -> bool:
        """发送到飞书"""
        try:
            import httpx
            from pathlib import Path
            import yaml

            config_path = Path.home() / ".config" / "hongjun" / "config.yaml"
            if not config_path.exists():
                return False
            config = yaml.safe_load(config_path.read_text())
            feishu = config.get("feishu", {})
            app_id = feishu.get("app_id", "")
            app_secret = feishu.get("app_secret", "")
            if not app_id or not app_secret:
                return False

            token_resp = httpx.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
                timeout=10,
            )
            token = token_resp.json().get("tenant_access_token", "")

            httpx.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": "oc_d860f9f653e3421db6ea419a81414cf6",
                    "msg_type": "text",
                    "content": json.dumps({"text": message}),
                },
                timeout=10,
            )
            return True
        except Exception as e:
            logger.error(f"飞书推送失败: {e}")
            return False

    # ── 评估 ───────────────────────────────────────────────────────────

    def mark_evaluated(self, repo: str, note: str):
        """标记某项目已评估"""
        if repo in self.findings:
            self.findings[repo].evaluated = True
            self.findings[repo].evaluation_note = note
            self._save()

    def get_unevaluated(self) -> list[Finding]:
        """获取未评估的发现"""
        return [f for f in self.findings.values() if not f.evaluated]

    def get_stats(self) -> dict:
        """获取发现统计"""
        all_f = list(self.findings.values())
        return {
            "total": len(all_f),
            "notified": sum(1 for f in all_f if f.notified),
            "evaluated": sum(1 for f in all_f if f.evaluated),
            "avg_relevance": round(sum(f.relevance_score for f in all_f) / len(all_f), 2) if all_f else 0,
        }


# ── 全局单例 ────────────────────────────────────────────────────────────────

_discovery: Optional[SkillDiscovery] = None


def get_discovery() -> SkillDiscovery:
    global _discovery
    if _discovery is None:
        _discovery = SkillDiscovery()
    return _discovery


# ── CLI 入口 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="鸿钧 · 技能主动发现")
    parser.add_argument("--notify", action="store_true", help="发现新项目时推送飞书")
    parser.add_argument("--dry-run", action="store_true", help="仅扫描，不推送也不保存")
    parser.add_argument("--force", action="store_true", help="强制重新扫描 GitHub（忽略缓存）")
    args = parser.parse_args()

    sd = get_discovery()
    findings = sd.scan_and_discover(force_refresh=args.force)

    print(f"发现 {len(findings)} 个相关项目：")
    for f in findings:
        status = "🆕" if f.notified else "📦"
        print(f"  {status} [{f.relevance_score:.2f}] {f.repo}")
        print(f"     {f.description[:80]}")

    if not args.dry_run and args.notify:
        notified = sd.notify_feishu()
        print(f"\n飞书推送: {'成功' if notified else '失败'}")

    stats = sd.get_stats()
    print(f"\n统计: 共 {stats['total']} 个记录，{stats['notified']} 个已通知")
