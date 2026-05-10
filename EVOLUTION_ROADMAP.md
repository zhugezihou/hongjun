# 鸿钧 · 进化路线图

> 目标：成为一个能理解意图、拆解任务、按部就班完成、交叉验证、保持记忆、主动学习的 Agent 系统

## 阶段划分

### 阶段1：夯实基础（能干活）✅
- [x] `agent.py` - 基础ReAct骨架（已存在）
- [x] `planner.py` - **任务分解引擎（Plan-and-Execute）** ✅ 2026-05-10
- [x] `task_executor.py` - **执行器（含交叉验证闭环）** ✅ 2026-05-10
- [x] `memory_injection.py` - **记忆注入** ✅ 2026-05-10
- [x] `task_state.py` - **任务状态持久化** ✅ 2026-05-10
- [x] `evolution_memory.py` - **集成反思引擎钩子** ✅ 2026-05-10
- [x] `reflection_engine.py` - **反思引擎（巩固/遗忘经验）** ✅ 2026-05-10
- [x] `scripts/daily_reflection.py` - **每日反思脚本** ✅ 2026-05-10
- [x] `hongjun-daily-evolution` cron - **09:00 每日反思** ✅ 2026-05-10
- [x] `orchestrator.py` - 集成 _llm_call（记忆注入） ✅ 2026-05-10

### 阶段2：自我进化（能反思）🔨
- [x] `error_pattern.py` - **错误模式积累：错误类型→修复方案映射** ✅ 2026-05-10
  - 内置8种常见错误模式（ImportError/GitError/TimeoutError等）
  - 每次失败自动记录，已知修复优先推荐
  - `self_repair._generate_fix()` 优先查库再 LLM 生成
- [x] `skill_discovery.py` - **主动技能发现：定期搜索GitHub trending** ✅ 2026-05-10
  - 扫描 AI agent / LangGraph / browser automation / memory system
  - 相关度评分，推送飞书朝堂群
  - 用户回复「研究 [repo]」可深入研究
- [ ] `self_evolution.py` - 与 error_pattern 深度集成（失败即记录到库）
- [ ] `cron/skill-discovery` - 每3天扫描一次 GitHub trending

### 阶段3：持续进化（能超越）
- [ ] `meta_learner.py` - 元学习：什么策略适合什么任务
- [ ] `self_improver.py` - 主动修改自身代码逻辑

## 核心原则

1. **每次commit必须可运行** — 不存在broken状态
2. **记忆驱动** — 每次失败都要形成可检索的经验
3. **反思闭环** — 定期复盘，不让错误经验积累也不让正确经验流失
4. **GitHub-first** — 本地改码 → git push → 部署机pull → restart

## 反思引擎设计

### 触发条件
- 每次任务完成后自动反思
- 每天定时全量反思（09:00）
- 连续失败3次同一类型任务时触发专项反思

### 反思操作
- **巩固**：成功的任务模式 → 写入 `skill_patterns`
- **遗忘**：连续失败3次的经验 → 标记为"失效"并降权
- **提炼**：从成功案例中提炼通用策略
- **修正**：从失败案例中修正错误假设

### 记忆生命周期
```
新经验 → 临时缓存 → 短期记忆(7天) → 长期记忆(活跃则保留，不活跃则遗忘)
                    ↓                    ↓
               高频访问 → 提升权重      低频访问 → 降权淘汰
```

## 已集成模块（白名单）

`self_repair.SAFE_TO_MODIFY` 中可自改的模块：
```
orchestrator, self_evolution, executor, tools,
intent_classifier, skill_manager, memory,
feishu_client, agent, evaluator, hindsight_integration,
cli, llm, logging_config, config,
reflection_engine, planner, task_executor, task_state,
memory_injection, error_pattern, skill_discovery
```

受保护模块（禁止自改）：`security`, `models`
