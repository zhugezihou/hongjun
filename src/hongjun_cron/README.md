# 鸿钧 Cron 调度系统

定时任务调度系统，集成于鸿钧 Gateway。

## 架构

```
┌─────────────────────────────────────────────┐
│           HongjunGateway (port 20830)        │
│  ┌─────────────┐    ┌──────────────────┐   │
│  │  HTTP API   │    │   CronScheduler   │   │
│  │  /cron/*    │    │  (后台调度线程)   │   │
│  └──────┬──────┘    └────────┬─────────┘   │
│         │                    │              │
│         │              ┌─────▼──────┐       │
│         │              │ CronDB     │       │
│         │              │(SQLite)    │       │
│         │              └─────┬──────┘       │
│         │                    │              │
│         │              ┌─────▼──────┐       │
│         │              │CronExecutor│       │
│         │              │(线程池 4)  │       │
│         │              └──┬─────┬──┘       │
└─────────┼─────────────────┘     │          │
          │          Orchestrator │  Webhook │
          ▼                        ▼          ▼
    飞书 朝堂群         鸿钧编排器      HTTP POST
```

## 调度类型

| 类型 | schedule_value 示例 | 说明 |
|------|---------------------|------|
| `cron` | `*/5 * * * *` | 标准 5 段 cron 表达式 |
| `interval` | `30m` / `2h` / `1d` | 固定间隔 |
| `once` | `2026-05-06T09:00:00` | 一次性，执行后自动完成 |

## HTTP API

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/cron/jobs` | 列出所有任务 |
| POST | `/cron/jobs` | 创建任务 |
| GET | `/cron/jobs/{id}` | 获取任务详情 |
| DELETE | `/cron/jobs/{id}` | 删除任务 |
| POST | `/cron/jobs/{id}/trigger` | 手动触发 |
| POST | `/cron/jobs/{id}/enable` | 启用 |
| POST | `/cron/jobs/{id}/disable` | 禁用 |
| GET | `/cron/status` | 调度器状态 |

### 创建任务

```bash
curl -X POST http://localhost:20830/cron/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "六部健康检查",
    "schedule_type": "interval",
    "schedule_value": "30m",
    "target_type": "orchestrator",
    "target_id": "main",
    "target_message": "六部健康检查"
  }'
```

## CLI

```bash
# 列出任务
hongjun-cron list

# 创建任务
hongjun-cron create \
  --name "六部健康检查" \
  --schedule interval --value 30m \
  --target orchestrator \
  --message "六部健康检查"

# 创建 cron 表达式任务
hongjun-cron create \
  --name "每日汇报" \
  --schedule cron --value "0 9 * * *" \
  --target webhook --to-url "https://example.com/hook" \
  --message '{"event":"daily"}'

# 手动触发
hongjun-cron trigger <job_id>

# 启用/禁用
hongjun-cron enable <job_id>
hongjun-cron disable <job_id>

# 执行历史
hongjun-cron history <job_id>

# 调度器状态
hongjun-cron status
```

## 调度器生命周期

- Gateway 启动时自动启动
- Gateway 关闭时自动停止
- 任务在独立线程池中执行（不阻塞 Gateway）
- 进程重启不丢失任务（SQLite 持久化）

## 执行目标

### Orchestrator（直接调用鸿钧编排器）

```python
target_type = "orchestrator"
target_id = ""              # 保留字段（未使用）
target_message = "六部健康检查"
```

直接调用 `HongjunGateway._call_orchestrator_impl(message=...)`，不依赖外部 Agent。

### Webhook（HTTP POST）

```python
target_type = "webhook"
target_id = "https://example.com/webhook"
target_message = '{"event":"daily_report"}'
```

发送 JSON POST 请求。

## 优先级

- `high`: 紧急任务，并发优先
- `normal`: 普通定时任务（默认）
- `low`: 后台维护任务

## 数据库

- 路径: `hongjun/db/hongjun_cron.db`
- 表: `cron_jobs`（任务定义）, `run_history`（执行历史）
