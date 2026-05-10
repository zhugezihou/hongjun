"""
github-ops Skill 实现
=====================

提供 GitHub 仓库操作的各个函数。
通过 gh CLI（优先）或 HTTP + GitHub API（降级）实现。
"""

import os
import json
import subprocess
import urllib.request
import urllib.parse
from datetime import date, timedelta


def search(
    query: str,
    language: str = None,
    sort: str = "stars",
    limit: int = 5,
) -> str:
    """
    搜索 GitHub 仓库。

    Args:
        query: 搜索关键词
        language: 编程语言（如 "Python", "JavaScript"）
        sort: 排序方式：stars / forks / updated
        limit: 返回结果数量（默认 5）
    """
    q = f"{query} in:name,description"
    if language:
        q += f" language:{language}"

    # 优先 gh CLI
    try:
        result = subprocess.run(
            ["gh", "api", "search/repositories",
             "--header", "X-GitHub-Api-Version:2022-11-28",
             "-f", f"q={q}",
             "-f", f"sort={sort}",
             "-f", f"per_page={limit}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            items = data.get("items", [])
            if items:
                lines = [f"🔍 GitHub 搜索: {query}" + (f" (language:{language})" if language else "")]
                for item in items[:limit]:
                    lines.append(f"  ★ {item.get('stargazers_count', 0)} | {item.get('full_name', '')}")
                    if item.get("description"):
                        lines.append(f"    {item['description'][:80]}")
                    lines.append(f"    🔗 {item.get('html_url', '')}")
                return "\n".join(lines)
    except Exception:
        pass

    # 降级：HTTP + GitHub API
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Hongjun/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    q_encoded = urllib.parse.quote(q)
    url = f"https://api.github.com/search/repositories?q={q_encoded}&sort={sort}&per_page={limit}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            items = data.get("items", [])
            if not items:
                return f"🔍 未找到 '{query}' 相关仓库"

            lines = [f"🔍 GitHub 搜索: {query}" + (f" (language:{language})" if language else "")]
            for item in items[:limit]:
                lines.append(f"  ★ {item['stargazers_count']} | {item['full_name']}")
                if item.get("description"):
                    lines.append(f"    {item['description'][:80]}")
                lines.append(f"    🔗 {item['html_url']}")
            return "\n".join(lines)
    except Exception as e:
        return f"❌ GitHub 搜索失败: {e}"


def trending(language: str = "Python", since: str = "daily") -> str:
    """
    查看 GitHub Trending。

    Args:
        language: 编程语言（默认 Python）
        since: 时间范围：daily / weekly / monthly
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Hongjun/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    date_map = {"daily": 1, "weekly": 7, "monthly": 30}
    days = date_map.get(since, 1)
    since_date = (date.today() - timedelta(days=days)).isoformat()
    # pushed:>=YYYY-MM-DD + language:Python（+ 和 : 是 GitHub 语法操作符，不编码）
    # 只编码日期值，操作符保留原样
    q = f"pushed:>={urllib.parse.quote(since_date)}+language:{language}"
    url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=10"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            items = data.get("items", [])
            if not items:
                return f"📊 {since} {language} Trending：无结果"

            lines = [f"📊 GitHub {since} Trending — {language}"]
            for i, item in enumerate(items, 1):
                lines.append(f"{i}. ★ {item['stargazers_count']} | {item['full_name']}")
                if item.get("description"):
                    lines.append(f"   {item['description'][:80]}")
            return "\n".join(lines)
    except Exception as e:
        return f"❌ Trending 获取失败: {e}"


def repo(owner: str, repo_name: str) -> str:
    """
    获取仓库基本信息。

    Args:
        owner: 仓库所有者
        repo_name: 仓库名
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Hongjun/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{owner}/{repo_name}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            item = json.loads(r.read())
            lines = [
                f"📦 {item['full_name']}",
                f"   ★ {item['stargazers_count']} stars | {item['forks_count']} forks",
            ]
            if item.get("description"):
                lines.append(f"   {item['description']}")
            if item.get("homepage"):
                lines.append(f"   🌐 {item['homepage']}")
            lines.append(f"   🔗 {item['html_url']}")
            if item.get("topics"):
                lines.append(f"   🏷️ {', '.join(item['topics'][:5])}")
            return "\n".join(lines)
    except Exception as e:
        return f"❌ 获取仓库信息失败: {e}"


def repo_file(owner: str, repo_name: str, path: str, ref: str = "main") -> str:
    """
    读取仓库中单个文件的内容。

    Args:
        owner: 仓库所有者
        repo_name: 仓库名
        path: 文件路径（如 "README.md"、"src/main.py"）
        ref: 分支/标签/SHA（默认 main）
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Hongjun/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/{path}"
    params = f"?ref={urllib.parse.quote(ref)}"
    try:
        req = urllib.request.Request(url + params, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            item = json.loads(r.read())
            if item.get("encoding") == "base64" and item.get("content"):
                import base64
                content = base64.b64decode(item["content"]).decode("utf-8", errors="replace")
                size = len(content)
                return f"📄 {owner}/{repo_name}/{path}@{ref} ({size} chars)\n\n{content[:8000]}"
            return f"📄 {owner}/{repo_name}/{path}@{ref}\n{item.get('content', '')[:8000]}"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return f"❌ 文件不存在: {owner}/{repo_name}/{path} (ref: {ref})"
        return f"❌ HTTP {e.code}: {e.reason}"
    except Exception as e:
        return f"❌ 读取文件失败: {e}"


def code_search(
    query: str,
    owner: str = None,
    repo: str = None,
    language: str = None,
    limit: int = 5,
) -> str:
    """
    在 GitHub 仓库中搜索代码（不是仓库）。

    Args:
        query: 搜索关键词
        owner: 可选，限定仓库所有者
        repo: 可选，限定具体仓库（需配合 owner）
        language: 可选，编程语言（如 "Python"）
        limit: 返回条数（默认 5）
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Hongjun/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    q = query
    if owner:
        q = f"repo:{owner}/{repo or '*'} {query}"
    if language:
        q += f" language:{language}"

    q_encoded = urllib.parse.quote(q)
    url = f"https://api.github.com/search/code?q={q_encoded}&per_page={limit}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            items = data.get("items", [])
            if not items:
                return f"🔍 代码搜索 '{query}': 无结果"

            lines = [f"🔍 代码搜索: {query}"]
            for item in items[:limit]:
                lines.append(f"  📄 {item['path']} in {item['repository']['full_name']}")
                lines.append(f"     🔗 {item['html_url']}")
                if item.get("text_matches"):
                    for m in item["text_matches"][:2]:
                        ctx = m.get("fragment", "")[:150]
                        lines.append(f"     │ {ctx}")
            return "\n".join(lines)
    except Exception as e:
        return f"❌ 代码搜索失败: {e}"


def tree(owner: str, repo_name: str, ref: str = "develop") -> str:
    """
    获取仓库目录树结构（快速了解项目布局）。

    Args:
        owner: 仓库所有者
        repo_name: 仓库名
        ref: 分支（默认 develop）
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Hongjun/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{owner}/{repo_name}/git/trees/{ref}?recursive=1"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            items = data.get("tree", [])
            dirs, files = {}, []
            for f in items:
                p = f["path"]
                parts = p.split("/")
                if len(parts) > 1:
                    dirs.setdefault(parts[0], set()).add(parts[1] if len(parts) > 1 else "")
                else:
                    files.append(p)
            lines = [f"🌳 {owner}/{repo_name} ({ref}) — {len(items)} items"]
            for d in sorted(dirs)[:20]:
                children = sorted(dirs[d])[:6]
                more = len(dirs[d]) - 6
                lines.append(f"  📁 {d}/ {' '.join(children)}" + (f" +{more}" if more > 0 else ""))
            if files:
                lines.append(f"  📄 根目录: {', '.join(files[:10])}")
            return "\n".join(lines)
    except Exception as e:
        return f"❌ 获取目录树失败: {e}"


def issues(owner: str, repo_name: str, state: str = "open", limit: int = 5) -> str:
    """
    获取仓库的 Issue 列表。

    Args:
        owner: 仓库所有者
        repo_name: 仓库名
        state: open / closed / all
        limit: 返回数量
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Hongjun/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{owner}/{repo_name}/issues?state={state}&per_page={limit}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            items = json.loads(r.read())
            # 过滤掉 PR（PR 也是 issue）
            items = [i for i in items if "pull_request" not in i]
            if not items:
                return f"📋 {owner}/{repo_name} 暂无 {state} issues"

            lines = [f"📋 {owner}/{repo_name} — {state} issues"]
            for item in items[:limit]:
                lines.append(f"  #{item['number']} {item['title']}")
                lines.append(f"   🔗 {item['html_url']}")
            return "\n".join(lines)
    except Exception as e:
        return f"❌ 获取 issues 失败: {e}"
