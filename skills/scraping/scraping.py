"""
scraping Skill 实现
===================

结构化数据抓取：JSON-LD、XPath、表格提取、CSV 导出。
"""

import httpx
import re
import csv
import json
from typing import Optional, List, Dict
from pathlib import Path


def extract_json_ld(url: str) -> str:
    """
    提取页面中的 JSON-LD 结构化数据。

    Args:
        url: 目标网页
    """
    try:
        resp = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        html = resp.text
        # 匹配 <script type="application/ld+json">...</script>
        pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
        matches = re.findall(pattern, html, re.DOTALL)
        if not matches:
            return "❌ 页面中未找到 JSON-LD 数据"

        results = []
        for m in matches[:5]:
            try:
                data = json.loads(m.strip())
                results.append(json.dumps(data, ensure_ascii=False, indent=2)[:500])
            except json.JSONDecodeError:
                continue

        if not results:
            return "❌ JSON-LD 解析失败"

        return "✅ JSON-LD 数据：\n" + "\n---\n".join(results[:3])
    except Exception as e:
        return f"❌ 抓取失败: {e}"


def extract_tables(url: str, path: str = "/tmp/table.csv") -> str:
    """
    提取页面中所有 HTML 表格并导出为 CSV。

    Args:
        url: 目标网页
        path: CSV 保存路径
    """
    try:
        from playwright.async_api import async_playwright
        import asyncio

        async def _inner():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=15000)
                tables = await page.query_selector_all("table")
                if not tables:
                    await browser.close()
                    return "❌ 页面中未找到表格"

                saved = 0
                for idx, table in enumerate(tables[:5]):
                    rows = await table.query_selector_all("tr")
                    data = []
                    for row in rows:
                        cells = await row.query_selector_all("th, td")
                        row_data = [await c.inner_text() for c in cells]
                        data.append(row_data)
                    if data:
                        with open(f"{path.rsplit('.', 1)[0]}_{idx}.csv", "w", newline="", encoding="utf-8") as f:
                            writer = csv.writer(f)
                            writer.writerows(data)
                        saved += 1
                await browser.close()
                return f"✅ 导出 {saved} 个表格到 {path.rsplit('/', 1)[0]}/"

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_inner())
        finally:
            loop.close()
    except Exception as e:
        return f"❌ 表格提取失败: {e}"


def xpath_extract(url: str, xpath: str, limit: int = 20) -> str:
    """
    用 XPath 提取页面元素。

    Args:
        url: 目标网页
        xpath: XPath 表达式，如 //h2/a | //div[@class='item']
        limit: 最多返回条数
    """
    try:
        from playwright.async_api import async_playwright
        import asyncio

        async def _inner():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=15000)
                elements = await page.query_selector_all(f"xpath={xpath}")
                results = []
                for el in elements[:limit]:
                    text = (await el.inner_text()).strip().replace("\n", " ")
                    href = await el.get_attribute("href") or ""
                    if text:
                        results.append(f"{text[:100]}" + (f" → {href}" if href else ""))
                    elif href:
                        results.append(f"[链接] {href}")
                await browser.close()
                return "✅ XPath 提取结果：\n" + "\n".join(results) if results else "❌ 未匹配到元素"

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_inner())
        finally:
            loop.close()
    except Exception as e:
        return f"❌ XPath 提取失败: {e}"


def batch_scrape(urls: List[str], selector: str = "h1,h2,h3,p") -> str:
    """
    批量抓取多个 URL 的文本内容。

    Args:
        urls: URL 列表（最多10个）
        selector: CSS 选择器（默认 h1,h2,h3,p）
    """
    if not urls:
        return "❌ URL 列表为空"
    urls = urls[:10]

    results = []
    for url in urls:
        try:
            resp = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            texts = [t.get_text(strip=True) for t in soup.select(selector) if t.get_text(strip=True)]
            snippet = " | ".join(texts[:5])[:200]
            results.append(f"✅ {url}\n   {snippet}")
        except Exception as e:
            results.append(f"❌ {url}: {e}")

    return "✅ 批量抓取结果：\n" + "\n".join(results)
