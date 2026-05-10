---
name: github-ops
description: |
  GitHub 仓库操作：搜索仓库/代码/文件/目录树 + Trending + Issues。
  通过 GitHub REST API（无需 gh CLI）。
triggers:
  - "github"
  - "仓库"
  - "趋势"
  - "trending"
  - "star"
  - "issue"
  - "pr"
  - "openrank"
  - "研究 源码"
  - "读 文件"
category: devops
version: "2.0"
author: "鸿钧·工部"
dependencies:
  - "python3 (标准库 json/urllib)"
tools:
  - shell
  - file_read
---

## github-ops Skill

### 功能（6个独立函数）
- `search` — 搜索仓库（按 stars / language / date）
- `repo` — 获取仓库基本信息（stars/forks/description）
- `tree` — 获取仓库目录树（快速了解项目布局）
- `repo_file` — 读取仓库中单个文件内容（研究源码必需）
- `code_search` — 在仓库中搜索代码（不是仓库名）
- `trending` — 查看 GitHub Trending
- `issues` — 查看 Issue/PR 列表

### 函数速查

#### search — 搜索仓库
```
search(query="MemPalace in:name,description", language="Python", sort="stars", limit=5)
```
→ 返回仓库名/★数/描述/链接

#### repo — 仓库信息
```
repo(owner="MemPalace", repo_name="mempalace")
```
→ 返回 stars/forks/描述/topics/首页

#### tree — 目录树
```
tree(owner="MemPalace", repo_name="mempalace", ref="develop")
```
→ 返回项目结构（前20目录，每目录显示前6子项）

#### repo_file — 读文件
```
repo_file(owner="MemPalace", repo_name="mempalace", path="README.md", ref="develop")
repo_file(owner="MemPalace", repo_name="mempalace", path="mempalace/layers.py", ref="develop")
```
→ 返回文件内容（前8000字符），支持任意分支/标签/SHA

#### code_search — 代码搜索
```
code_search(query="class Layer0", owner="MemPalace", repo="mempalace", limit=5)
code_search(query="BM25 scores", language="Python", limit=5)
```
→ 返回匹配文件路径 + 代码片段上下文

#### trending — Trending
```
trending(language="Python", since="daily")
```

#### issues — Issues
```
issues(owner="MemPalace", repo_name="mempalace", state="open", limit=5)
```

### 参数总表

| 函数 | 必填参数 | 常用可选 |
|------|---------|---------|
| search | query | language, sort, limit |
| repo | owner, repo_name | — |
| tree | owner, repo_name | ref（默认 develop） |
| repo_file | owner, repo_name, path | ref（默认 main） |
| code_search | query | owner, repo, language, limit |
| trending | — | language, since |
| issues | owner, repo_name | state, limit |

### 研究项目源码的标准流程
1. `tree` → 了解目录结构
2. `repo` → 确认项目基本信息
3. `repo_file` → 读 README.md
4. `repo_file` → 按需读核心源码文件
5. `code_search` → 找特定函数/类的实现位置

### 注意事项
- 默认分支注意：MemPalace 用 `develop`，不是 `main`
- 读文件截取前 8000 字符，超大文件分次读
- 未认证 API 速率限制 60 req/hr，配 GITHUB_TOKEN 可达 5000 req/hr
- `repo_file` 对 base64 编码文件自动解码
