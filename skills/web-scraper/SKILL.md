---
name: web-scraper
description: |
  从网页抓取结构化内容（标题/正文/链接/图片）。
  支持 CSS 选择器和 XPath，适用于新闻、文档、产品页等。
triggers:
  - "抓取"
  - "提取网页"
  - "scrape"
  - "网页内容"
  - "解析网页"
category: web
version: "1.0"
author: "鸿钧·礼部"
dependencies:
  - "pip install requests beautifulsoup4"
tools:
  - shell
  - file_read
---

## web-scraper Skill

### 功能
- 输入 URL，返回页面标题/正文/链接/图片
- 支持 CSS 选择器自定义提取规则
- 支持 JSON 输出格式

### 使用方式
```
scrape(url="https://example.com", selector="article", format="markdown")
```

### 参数
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| url | str | ✅ | 目标网页 URL |
| selector | str | 否 | CSS 选择器，默认提取 `<article>` 或 `<main>` |
| format | str | 否 | 输出格式：`markdown`（默认）/ `text` / `json` |

### 示例
**输入**：`scrape("https://news.ycombinator.com", selector=".titleline", format="text")`
**输出**：返回标题列表

### 注意事项
- 自动设置 User-Agent 避免被封
- 超时 10 秒
- 仅用于合法用途
