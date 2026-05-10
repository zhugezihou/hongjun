# 鸿钧 · 项目规格书

> **鸿钧** = 六部协同的超强 Agent 系统

## 愿景

让 AI Agent 真正像人一样：能记忆、能工具、能协作、能自省。
不是一个玩具，而是一个可以从零组装、生产可用的 Agent 基础设施。

---

## 技术选型（已确认）

| 组件 | 选型 | 版本 | 状态 |
|------|------|------|------|
| 核心编排 | LangGraph | 1.1.10 | ✅ 已装 |
| 记忆系统 | MemPalace | 3.3.4 | ✅ 已装 |
| 工具层 | browser-use | 0.12.6 | ✅ 已装 |
| 安全护栏 | NeMo Guardrails | 0.21.0 | ⬜ 待装 |
| 评测体系 | 自研规则 | - | ✅ 已集成 |
| 通信协议 | A2A + MCP | - | ✅ 自研 |

---

## 架构总览

```
用户请求
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│                    鸿钧 · 吏部（编排层）                  │
│         Microsoft Agent Framework / LangGraph            │
│         负责任务分解 + Agent 调度 + 结果汇总              │
└────────────────────────┬────────────────────────────────┘
                         │ A2A 协议
      ┌──────────────────┼──────────────────┐
      ▼                  ▼                  ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────────┐
│  工部·执行  │  │  户部·记忆  │  │   礼部·工具层   │
│  LangGraph  │  │  MemPalace │  │   browser-use   │
│  状态流编排  │  │  记忆检索   │  │   浏览器自动化   │
└─────────────┘  └─────────────┘  └─────────────────┘
      │                  │                  │
      └──────────────────┼──────────────────┘
                         │
      ┌──────────────────┼──────────────────┐
      ▼                  ▼                  ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────────┐
│  兵部·安全  │  │  刑部·评测  │  │   中书令·协调   │
│ NeMo Guard  │  │  DeepEval   │  │   监控/日志     │
│  输入/输出过滤│  │  质量评估   │  │   链路追踪     │
└─────────────┘  └─────────────┘  └─────────────────┘
```

---

## 目录结构

```
hongjun/
├── SPEC.md                    # 本文件
├── README.md                  # 项目说明
├── requirements.txt           # Python 依赖
├── config/
│   └── hongjun.yaml          # 配置文件
├── src/
│   └── hongjun/
│       ├── __init__.py
│       ├──吏部_coordinator.py     # 任务编排（LangGraph）
│       ├──工部_executor.py        # 执行引擎
│       ├──户部_memory.py          # MemPalace 记忆层
│       ├──礼部_tools.py          # 工具注册（browser-use）
│       ├──兵部_guardrails.py      # NeMo Guardrails 安全
│       ├──刑部_evaluation.py     # DeepEval 质量评估
│       ├──中书令_monitor.py      # 监控/日志/追踪
│       └──protocol/
│           ├── a2a_server.py     # A2A 服务端
│           └── a2a_client.py     # A2A 客户端
└── tests/
    └── test_hongjun.py
```

---

## 各部职责（详细）

### 吏部 · Coordinator（任务编排）

**职责**：接收用户请求 → 分解任务 → 分发给各部 → 汇总结果

**技术**：LangGraph 的 `StateGraph`
- `RequestState`：用户请求 / 任务列表 / 执行结果
- 节点：分解任务 → 分发工部 → 分发礼部 → 汇总结果
- 边：条件分支（成功/失败/需要人工确认）

```python
# 核心决策
if "搜索" in 请求 or "查" in 请求:
    → 分发礼部（工具执行）
elif "写代码" in 请求 or "开发" in 请求:
    → 分发工部（执行引擎）
else:
    → 协调多部联合执行
```

### 工部 · Executor（执行引擎）

**职责**：代码生成 / 文件操作 / 命令执行

**技术**：LangGraph 的 ToolNode
- 内置工具：文件读写 / Shell 命令 / Git 操作
- Agent 模式：ReAct（Thought → Action → Observation）

### 户部 · Memory（记忆系统）

**职责**：会话记忆 / 长期记忆 / 记忆检索

**技术**：MemPalace 3.3.4
- 三层记忆：短期（上下文窗口）/ 中期（SQLite）/ 长期（向量检索）
- `MemoryStore`：记忆的 CRUD 接口
- `EpisodicBuffer`：重要事件打标记入库

```python
from mempalace import MemoryPalace

mp = MemoryPalace()
mp.remember("用户偏好中文", importance=0.9)
context = mp.recall("用户有什么偏好")
```

### 礼部 · Tools（工具层）

**职责**：扩展 Agent 能力边界（搜索/浏览器/计算）

**技术**：browser-use 0.12.6
- `BrowserTool`：控制浏览器完成表单/搜索/数据抓取
- `SearchTool`：网页搜索 + 内容摘要（Jina AI）
- `CalculatorTool`：高精度数学计算

### 兵部 · Guardrails（安全护栏）

**职责**：输入过滤 / 输出审核 / 权限管控

**技术**：NeMo Guardrails 0.21.0
- 输入 rails：在请求进入 Agent 前过滤恶意 Prompt
- 输出 rails：在结果返回用户前过滤敏感内容
- 主题 rails：禁止谈论特定话题

```python
from nemoguardrails import RailsConfig, LLMRails

config = RailsConfig.from_path("config/")
rails = LLMRails(config)
safe_response = rails.generate(prompt=user_input)
```

### 刑部 · Evaluation（质量评估）

**职责**：每次任务后自动评估质量 / 生成报告

**技术**：DeepEval
- 正确性：答案是否符合预期
- 完整性：是否覆盖所有子任务
- 安全性：是否有数据泄露风险

### 中书令 · Monitor（协调监控）

**职责**：链路追踪 / 日志聚合 / 性能监控 / 告警

**技术**：自研 + OpenTelemetry
- 每次 A2A 调用记录 span
- 慢任务告警（>60s）
- 任务成功率统计

---

## 通信协议

### A2A（Agent to Agent）

```
吏部（Coordinator）
  ├─ SendMessage{agent_id: "dev", task: "写代码"}
  │     └─→ 工部 A2A Server (端口 20021)
  ├─ SendMessage{agent_id: "hubu", task: "记忆检索"}
  │     └─→ 户部 A2A Server (端口 20022)
  └─ SendMessage{agent_id: "content", task: "网页搜索"}
        └─→ 礼部 A2A Server (端口 20023)
```

**A2A 端口分配**：
- 吏部（协调）：20020
- 工部（执行）：20021
- 户部（记忆）：20022
- 礼部（工具）：20023
- 兵部（安全）：20024
- 刑部（评测）：20025

### MCP（Model Context Protocol）

工具发现协议，由礼部暴露工具清单，供其他 Agent 按需调用。

---

## 工作流程示例

```
用户：帮我查一下今天 GitHub 最火的 AI Agent 项目

① 吏部（接收）→ 解析请求，确认需要网页搜索
② 礼部（工具）→ browser-use 搜索 GitHub Trending
③ 户部（记忆）→ 检索历史是否有相关信息
④ 工部（执行）→ 可选：若需要代码示例则执行
⑤ 刑部（评测）→ 评估搜索结果质量
⑥ 兵部（安全）→ 全程输入输出过滤
⑦ 吏部（汇总）→ 整理结果，返回用户
⑧ 户部（存储）→ 记住这次查询（用于后续上下文）
```

---

## 安装依赖

```bash
pip install langgraph>=1.1.10
pip install mempalace>=3.3.4
pip install browser-use>=0.12.6    # 需要 playwright 已装
pip install nemoguardrails>=0.21.0  # 需要 PyTorch
pip install deepeval>=0.21.0
pip install openai>=1.0            # LLM 调用
pip install httpx aiohttp          # 网络请求
pip install pyyaml                  # 配置文件
pip install structlog                # 结构化日志
```

---

## Roadmap（补齐缺口，按依赖排序）

> 目标：让鸿钧从"能跑的 Python 脚本"变成"真正的 Agent 系统"

### 🔴 Phase G1 — Gateway 进程（根基）✅ 完成
- [x] HTTP Gateway（port 20830）：aiohttp + uvicorn 异步服务器
- [x] Session 状态机：NEW → ACTIVE → IDLE → COMPRESSING → DONE
- [x] 会话存储：SQLite `hongjun_sessions.db`（sessions / messages / crons 三表）
- [x] 心跳机制：last_active_at 自动更新
- [x] 请求队列：并发管理（max 4 并发，PriorityQueue 支持 HIGH/NORMAL/LOW）
- [x] CLI 入口：`hongjun start/stop/status/chat/sessions/metrics`
- [x] Systemd 服务：`deploy/hongjun-gateway.service`

### 🔴 Phase G2 — LLM 真实集成 ✅ 完成
- [x] LLM 模块：`src/hongjun/llm.py`（统一 chat 接口，支持 MiniMax/OpenAI/DeepSeek）
- [x] 模型抽象层：自动从模型名推断 provider（resolve_provider）
- [x] Gateway 集成：`_call_llm()` 已接入，失败时优雅降级
- [x] MiniMax API Key：环境变量 `MINIMAX_API_KEY` 已配置
- [x] 流式输出（Server-Sent Events）：`_stream()` provider 分发表，按 model 自动分发

### 🔴 Phase G3 — CLI 入口 ✅ 完成
- [x] `hongjun` 命令：`start / stop / status / chat / sessions / metrics`
- [x] 安装到 `~/.local/bin/hongjun`
- [x] Systemd 服务：`deploy/hongjun-gateway.service`
- [x] 配置文件：`~/.config/hongjun/config.yaml`

### 🔴 Phase G4 — 飞书通道 ✅ 基本完成
- [x] 独立飞书 Bot（app_id: cli_a9334eb4cef85ccd）
- [x] WebSocket 连接（lark-oapi SDK v2，auto_reconnect）
- [x] 群消息监听（oc_d860f9f653e3421db6ea419a81414cf6）
- [x] @mention 触发响应（消息路由 + 简单对话流式返回）
- [x] 消息持久化（通过 session 避免重复响应）
- [x] session split 问题修复（gateway 重启恢复）

### 🔴 Phase G5 — MCP 集成 ✅ 运行中
- [x] MCP Server 运行在 port 20831（HTTP Streamable）
- [x] 53 个工具已注册（MCP server tools）
- [x] Skills 系统：7 个 skills 已加载（GitHub / Weather / Scraping 等）
- [ ] 工具 → 鸿钧工具转换层（编排器直接调 MCP）
- [ ] 动态工具发现

### 🔴 Phase G6 — Skills 系统 🟡 部分完成
- [x] Skills 目录：`~/.config/hongjun/skills/`
- [x] Skill 加载机制：skill_view / skill_manage
- [x] Skill 编写规范（SKILL.md 格式）
- [x] 内置 Skills：网页搜索（blogwatcher）/ GitHub / 天气 / scraping
- [ ] Skill 触发词自动发现（意图分类 → Skill 映射）

### 🟡 Phase G7 — 浏览器自动化 ⬜ 未开始
- [ ] CDP 连接（复用 Hermes 的 headless Chrome）
- [ ] browser-use 集成
- [ ] 页面截图 / 点击 / 填表

### 🟡 Phase G8 — 记忆系统 ⬜ 未开始
- [ ] 户部对接 MemPalace CLI（subprocess 调用）
- [ ] 向量记忆（OpenClaw 的 vector-memory 经验复用）
- [ ] Context Compaction：会话过长时自动压缩
- [ ] 记忆持久化到 SQLite

### 🟡 Phase G9 — Cron 系统 🟡 基础完成
- [x] 定时任务调度（intervel / cron 两种模式）
- [x] 定时健康检查 job（每 15 分钟一次）
- [ ] Cron 任务存储：SQLite 持久化
- [ ] 任务队列 + 重试机制

### 🟡 Phase G10 — 安全护栏 🟡 基础完成
- [x] 危险操作预审批（DANGEROUS_PATTERNS，shell/rm/kill 等）
- [x] 命令白名单（allowed_commands / blocked_commands）
- [ ] 输入过滤（关键词 / 模式匹配）
- [ ] 输出审核
- [ ] 权限级别（GUEST / USER / ADMIN）

---

*最后更新：2026-05-10*
