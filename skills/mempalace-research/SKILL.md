---
name: mempalace-research
description: |
  研究 MemPalace 记忆系统的完整工作流。
  通过 GitHub API 读取源码和文档，输出架构分析报告。
triggers:
  - "MemPalace"
  - "记忆系统 研究"
  - "AI 记忆 架构"
  - "mempalace research"
category: research
version: "1.0"
author: "鸿钧·礼部"
dependencies:
  - "python3 (标准库 json/urllib)"
tools:
  - shell
  - file_read
---

## mempalace-research Skill

### 功能
研究 MemPalace（★ 51k，GitHub 最大开源记忆系统）的架构实现，输出结构化分析报告。

### 研究流程

**Step 1：定位仓库**
```
search(query="MemPalace in:name,description,readme", sort="stars")
→ 确认为 MemPalace/mempalace
```

**Step 2：了解项目结构**
```
tree(owner="MemPalace", repo_name="mempalace", ref="develop")
→ 327 个文件，核心在 mempalace/ 目录
```

**Step 3：读取核心文件**
按以下顺序读取（每文件取前 200 行）：

| 文件 | 内容 |
|------|------|
| `README.md` | 项目定位、核心卖点、基准数据 |
| `mempalace/layers.py` | 4层记忆栈架构（L0-L3） |
| `mempalace/backends/base.py` | 存储后端接口（RFC 001） |
| `mempalace/palace.py` | 收藏库操作（Wing/Room/Drawer） |
| `mempalace/searcher.py` | 混合搜索（BM25+语义向量） |
| `mempalace/knowledge_graph.py` | 时序知识图谱（SQLite） |
| `mempalace/mcp_server.py` | MCP 协议服务端（29工具） |

**Step 4：输出分析报告**
按以下结构组织输出：
1. 项目概览（stars/forks/语言/核心卖点）
2. 记忆架构（4层栈 + Wing/Room/Drawer 模型）
3. 存储设计（可插拔后端 + ChromaDB 默认）
4. 检索机制（BM25 + 向量混合搜索）
5. 知识图谱（时序 SQLite 图）
6. MCP 集成（29工具清单）
7. 基准性能（96.6% R@5 无 LLM）
8. 与鸿钧现有设计的对比分析

### 关键发现（已有研究结论）

**MemPalace 核心架构：**
- **4层记忆栈**：L0身份(100t常驻) → L1精华(500-800t常驻) → L2按需(200-500t) → L3深度搜索(无限)
- **Wing/存储模型**：Wing=人/项目，Room=话题，Drawer=单条原文记忆
- **核心原则**：原文 verbatim 存储，不摘要，不过滤
- **96.6% R@5**（LongMemEval，纯语义无 LLM）
- **可插拔后端**：BaseBackend ABC + ChromaDB/registry 系统
- **时序知识图谱**：SQLite 存储实体关系（subject/predicate/object + valid_from/to）
- **MCP 29工具**：覆盖 palace 读写、graph 操作、cross-wing 导航

**对比鸿钧记忆系统：**
| 维度 | 鸿钧 | MemPalace |
|------|------|-----------|
| 存储 | 平面 JSON + SQLite | ChromaDB 向量 + SQLite 图 |
| 检索 | LLM 摘要增强 | 纯语义 BM25+向量，无 LLM |
| 层级 | 无层级 | 4层按需加载 |
| 图谱 | 无 | 时序知识图谱 |
| 结构 | Wing/Room/Drawer | 会话/项目分离 |
| MCP | 无（待接） | 29工具原生 MCP |

### 注意事项
- 优先读 `develop` 分支（最新代码）
- 大文件（如 `layers.py` 17KB）截取前 200 行理解架构
- 代码搜索用 `code_search` 找特定函数实现
