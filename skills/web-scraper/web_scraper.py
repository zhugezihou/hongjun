"""
web-scraper Skill 实现
======================

从网页提取结构化内容。
支持 CSS 选择器和 XPath，适用于新闻、文档、产品页等。
"""

import httpx
import re
from typing import Optional


def scrape(
    url: str,
    selector: str = "article",
    format: str = "markdown",
) -> str:
    """
    从网页抓取内容。

    Args:
        url: 目标网页 URL
        selector: CSS 选择器（默认 'article'，无匹配时回退到 <main> 或 <body>）
        format: 输出格式：markdown（默认）/ text / json

    Returns:
        提取的页面内容（字符串）
    """
    try:
        resp = httpx.get(
            url,
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Hongjun/1.0; +http://github.com)"
            },
            follow_redirects=True,
        )
        resp.raise_for_status()
        html = resp.text
    except httpx.Timeout:
        return f"⏰ 超时（>10s）"
    except httpx.HTTPStatusError as e:
        return f"❌ HTTP {e.response.status_code}: {url}"
    except Exception as e:
        return f"❌ 请求失败: {e}"

    # 清理 HTML：移除 script / style / comments
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # 提取 <title>
    title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else ""

    # 简单 CSS 选择器模拟：tag.class#id 形式
    # 支持：article, main, .class, #id, tag.class
    content = ""
    if selector:
        content = _css_select(html, selector)

    if not content:
        # 回退策略
        for fallback in ["<main", "<article", "<body"]:
            content = _css_select(html, fallback)
            if content:
                break

    # 标签清理
    text = _html_to_text(content or html)

    if format == "json":
        import json
        return json.dumps(
            {"url": url, "title": title, "text": text[:3000]},
            ensure_ascii=False,
        )
    elif format == "text":
        return (f"标题: {title}\n\n" if title else "") + text[:3000]
    else:
        # markdown 风格
        md = f"# {title}\n\n" if title else ""
        md += text[:3000]
        return md


def _css_select(html: str, selector: str) -> str:
    """
    简化的 CSS 选择器提取。

    支持：
      - tag          → <tag>...</tag>
      - tag.class    → <tag class="...">...</tag>
      - tag#id       → <tag id="...">...</tag>
      - .class       → 任意 class
      - #id          → 任意 id
    """
    selector = selector.strip()

    # #id 形式
    id_match = re.search(rf'<(\w+)[^>]*\bid=["\']?{re.escape(selector[1:])}["\']?[^>]*>(.*?)</\1>',
                          html, re.DOTALL | re.IGNORECASE)
    if selector.startswith("#") and id_match:
        return id_match.group(2)

    # .class 形式
    if selector.startswith("."):
        cls = selector[1:]
        pattern = rf'<(\w+)[^>]*\bclass=["\'][^"\']*{re.escape(cls)}[^"\']*["\'][^>]*>(.*?)</\1>'
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(2)

    # tag.class 或 tag#id 或 tag
    tag_match = re.match(r'^(\w+)([.#][\w-]+)?$', selector)
    if tag_match:
        tag = tag_match.group(1)
        extra = tag_match.group(2) or ""

        if extra.startswith("."):
            cls = extra[1:]
            pattern = rf'<{tag}[^>]*\bclass=["\'][^"\']*{re.escape(cls)}[^"\']*["\'][^>]*>(.*?)</{tag}>'
        elif extra.startswith("#"):
            id_val = extra[1:]
            pattern = rf'<{tag}[^>]*\bid=["\']?{re.escape(id_val)}["\']?[^>]*>(.*?)</{tag}>'
        else:
            pattern = rf'<{tag}[^>]*>(.*?)</{tag}>'

        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1)

    # 裸 tag（如 article, main）
    pattern = rf'<{re.escape(selector)}[^>]*>(.*?)</{selector}>'
    match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1)

    return ""


def _html_to_text(html: str) -> str:
    """HTML → 可读文本"""
    # 换行处理
    text = re.sub(r'<(br|p|div|li|tr|h[1-6])[^>]*>', '\n', html, flags=re.IGNORECASE)
    # 移除剩余标签
    text = re.sub(r'<[^>]+>', '', text)
    # 实体解码
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
    text = text.replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    # 合并空白
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n\n', text).strip()
    return text
