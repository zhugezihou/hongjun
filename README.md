# 鸿钧 · 六部尚书协同 Agent 系统

> 让 AI Agent 真正像人一样：能记忆、能工具、能协作、能自省。

**鸿钧**是一个基于六部尚书协同架构的 Agent 系统，定位与 Hermes、OpenClaw 同级。

---

## 项目状态

| Phase | 内容 | 状态 |
|-------|------|------|
| G1 | Gateway 进程 | ✅ 完成 |
| G2 | LLM 集成 | ✅ 代码完成（需配 key） |
| G3 | CLI 入口 | ✅ 完成 |
| G4 | 飞书通道 | ⬜ 待做 |
| G5 | MCP 集成 | ⬜ 待做 |
| G6 | Skills 系统 | ⬜ 待做 |
| G7 | 浏览器自动化 | ⬜ 待做 |
| G8 | 记忆系统真集成 | ⬜ 待做 |
| G9 | Cron 系统 | ⬜ 待做 |
| G10 | 安全护栏 | ⬜ 待做 |

---

## 架构

```
                    ┌─────────────────────────────────┐
                    │  鸿钧 Gateway  (port 20830)      │
                    │  aiohttp + uvicorn 异步 HTTP    │
                    └──────────┬──────────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
   ┌────▼────┐          ┌─────▼─────┐         ┌─────▼─────┐
   │六部 Handler│         │ Session DB │         │Request Queue│
   │吏部/工部...│         │ SQLite    │         │ max 4 并发  │
   └─────────┘          └───────────┘         └───────────┘

用户请求 → Gateway → 吏部编排 → 工部执行 → 刑部评测 → 兵部护栏 → Gateway → 用户
```

---

## 快速开始

### 1. 配置 API Key（必须）

```bash
# MiniMax API Key（必须有）
export MINIMAX_API_KEY=sk-cp-your-key-here
```

### 2. 启动 Gateway

```bash
# 方式1：直接启动（后台运行）
hongjun start

# 方式2：systemd（开机自启）
systemctl --user start hongjun-gateway.service
```

### 3. 使用

```bash
# 查看状态
hongjun status

# 聊天
hongjun chat "你好鸿钧"

# 列出所有会话
hongjun sessions

# 性能指标
hongjun metrics
```

### 4. HTTP API

```bash
# 聊天
curl -X POST http://localhost:20830/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "写一个快排算法"}'

# 状态
curl http://localhost:20830/status

# 会话列表
curl http://localhost:20830/sessions
```

---

## 目录结构

```
hongjun/
├── src/hongjun/
│   ├── __init__.py
│   ├── llm.py                 # LLM 统一接口（MiniMax/OpenAI/DeepSeek）
│   ├── gateway/
│   │   ├── server.py          # HTTP Gateway（aiohttp）
│   │   ├── session.py          # Session 状态机
│   │   ├── db.py              # SQLite 存储
│   │   └── queue.py           # 请求队列
│   ├── 六部/                   # Agent 模块（Phase G4+ 陆续接入）
│   │   ├── 吏部_coordinator.py
│   │   ├── 工部_executor.py
│   │   ├── 户部_memory.py
│   │   ├── 礼部_tools.py
│   │   ├── 兵部_guardrails.py
│   │   └── 刑部_evaluation.py
│   └── protocol/
│       ├── a2a_server.py
│       └── a2a_client.py
├── scripts/
│   └── hongjun                # CLI 入口
├── deploy/
│   ├── hongjun-gateway.service  # systemd 服务
│   ├── Dockerfile
│   └── docker-compose.yaml
└── db/                         # SQLite 数据库
    └── hongjun_sessions.db
```

---

## Gateway API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/chat` | POST | 发送消息，返回 LLM 响应 |
| `/status` | GET | Gateway 健康状态 |
| `/sessions` | GET | 列出所有会话 |
| `/sessions/{id}` | GET | 会话详情（含消息历史） |
| `/sessions/{id}/shutdown` | POST | 关闭会话 |
| `/shutdown` | POST | 关闭 Gateway |
| `/metrics` | GET | 性能指标 |
| `/health` | GET | 简洁健康检查 |

---

## Session 状态机

```
  NEW → ACTIVE → IDLE → COMPRESSING → DONE
            ↑         │
            └─────────┘ (新消息)
```

- **NEW**: 会话刚创建
- **ACTIVE**: 正在处理请求
- **IDLE**: 等待新消息（超过 50 条自动压缩）
- **COMPRESSING**: 正在进行 Context Compaction
- **DONE**: 会话已结束

---

## 配置

关键配置在 `~/.config/hongjun/config.yaml` 或环境变量：

```bash
export MINIMAX_API_KEY=sk-cp-...     # 必须
export OPENAI_API_KEY=sk-...         # 可选（OpenAI 模型）
export HONGJUN_PORT=20830            # Gateway 端口（默认）
```

---

## 与 Hermes / OpenClaw 的关系

| 维度 | Hermes | OpenClaw | 鸿钧 |
|------|--------|----------|------|
| 定位 | 六部中枢 Agent | 六部尚书平台 | 六部尚书协同系统 |
| Gateway | ✅ Hermes Gateway | ✅ OpenClaw Gateway | ✅ 鸿钧 Gateway |
| 模型 | MiniMax | 多模型 | 多模型 |
| 飞书通道 | ✅ | ✅ | Phase G4 |
| MCP | ✅ | ❌ | Phase G5 |
| Skills | ✅ | ✅ | Phase G6 |
| 浏览器 | ✅ | ✅ | Phase G7 |
| 记忆 | ✅ | ✅ | Phase G8 |
| Cron | ✅ | ✅ | Phase G9 |

---

*最后更新：2026-05-05*
