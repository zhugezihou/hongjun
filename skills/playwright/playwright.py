"""
playwright Skill 实现
=====================

浏览器自动化：渲染 JS 动态页面、截图、提取内容、填表、点击。
"""

import asyncio
from typing import Optional


async def _run_playwright(coro):
    """在事件循环中运行 playwright 异步协程。"""
    return await coro


def screenshot(url: str, path: str = "/tmp/screenshot.png", full_page: bool = False) -> str:
    """
    对 URL 进行截图。

    Args:
        url: 目标网页
        path: 保存路径（默认 /tmp/screenshot.png）
        full_page: 是否截取整页（默认否，只截可视区）
    """
    async def _inner():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=15000)
            await page.screenshot(path=path, full_page=full_page)
            await browser.close()
            return path

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_inner())
    finally:
        loop.close()


def extract_text(url: str, selector: str = "body") -> str:
    """
    提取页面上指定选择器的文本内容。

    Args:
        url: 目标网页
        selector: CSS 选择器（默认 body）
    """
    async def _inner():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=15000)
            elem = await page.query_selector(selector)
            text = await elem.inner_text() if elem else ""
            await browser.close()
            return text[:3000]  # 最多返回3000字符

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_inner())
    finally:
        loop.close()


def extract_links(url: str, selector: str = "a") -> str:
    """
    提取页面上所有链接（href + 文字）。

    Args:
        url: 目标网页
        selector: CSS 选择器（默认 a）
    """
    async def _inner():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=15000)
            links = await page.query_selector_all(selector)
            results = []
            for link in links[:30]:  # 最多30条
                href = await link.get_attribute("href")
                text = await link.inner_text()
                text = text.strip().replace("\n", " ")[:50]
                if href:
                    results.append(f"{text} → {href}")
            await browser.close()
            return "\n".join(results) if results else "未找到链接"

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_inner())
    finally:
        loop.close()


def scroll_and_extract(url: str, selector: str = "article", max_scrolls: int = 3) -> str:
    """
    滚动页面并提取内容（适用于无限滚动页面）。

    Args:
        url: 目标网页
        selector: 内容选择器
        scrolls: 滚动次数（默认3）
    """
    async def _inner():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=15000)
            for _ in range(max_scrolls):
                await page.evaluate("window.scrollBy(0, 800)")
                await page.wait_for_timeout(800)
            items = await page.query_selector_all(selector)
            results = []
            for item in items[:20]:
                t = await item.inner_text()
                if t and len(t) > 20:
                    results.append(t.strip()[:200])
            await browser.close()
            return "\n---\n".join(results) if results else "未提取到内容"

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_inner())
    finally:
        loop.close()
