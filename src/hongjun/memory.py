"""
户部 · 记忆系统
=================

基于 MemPalace CLI + SQLite/ChromaDB 双轨记忆架构。

三层记忆：
  短期记忆  → LLM 上下文窗口（会话级）
  中期记忆  → MemPalace（CLI subprocess，SQLite 持久化）
  长期记忆  → ChromaDB 向量检索

MemPalace 是 CLI 工具，通过 subprocess 调用。
同时提供轻量级 SQLite 记忆作为降级方案。

用法：
  memory = HongjunMemory(user_id="皇上")
  memory.remember("用户喜欢中文交流", importance=0.9)
  context = memory.recall("用户有什么偏好")
"""

import sqlite3
import json
import os
import subprocess
import uuid
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any


# === SQLite 轻量记忆（降级方案）===

class SQLiteMemory:
    """
    轻量级 SQLite 记忆存储

    不依赖任何外部服务，本地文件持久化。
    用于无法使用 MemPalace 时的降级方案。
    """

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                last_accessed TEXT
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance DESC)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_created ON memories(created_at DESC)
        """)
        self.conn.commit()

    def add(self, content: str, importance: float = 0.5, tags: List[str] = None) -> str:
        memory_id = str(uuid.uuid4())[:12]
        now = datetime.now().isoformat()
        tags_json = json.dumps(tags or [])
        self.conn.execute(
            "INSERT INTO memories (id, content, importance, tags, created_at, last_accessed) VALUES (?, ?, ?, ?, ?, ?)",
            (memory_id, content, importance, tags_json, now, now),
        )
        self.conn.commit()
        return memory_id

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """简单关键词搜索"""
        cursor = self.conn.execute(
            """
            SELECT id, content, importance, tags, created_at
            FROM memories
            WHERE content LIKE ? OR tags LIKE ?
            ORDER BY importance DESC, last_accessed DESC
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", top_k),
        )
        rows = cursor.fetchall()
        results = []
        for r in rows:
            results.append({
                "id": r[0],
                "content": r[1],
                "importance": r[2],
                "tags": json.loads(r[3]),
                "timestamp": r[4],
            })
        return results

    def get_recent(self, limit: int = 10) -> List[Dict]:
        cursor = self.conn.execute(
            "SELECT id, content, importance, tags, created_at FROM memories ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        return [
            {
                "id": r[0],
                "content": r[1],
                "importance": r[2],
                "tags": json.loads(r[3]),
                "timestamp": r[4],
            }
            for r in rows
        ]

    def delete(self, memory_id: str) -> bool:
        cursor = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()
        return cursor.rowcount > 0


# === Wing/Room 路由器 ===

import re as _re

# Hall types (hall_* in MemPalace):
HALL_FACTS = "hall_facts"
HALL_EVENTS = "hall_events"
HALL_DISCOVERIES = "hall_discoveries"
HALL_PREFERENCES = "hall_preferences"
HALL_ADVICE = "hall_advice"

# Wing is always 'hongjun' — this palace belongs to the 鸿钧 agent
DEFAULT_WING = "hongjun"

# Keyword → Room 路由表（按优先级排序）
_ROOM_KEYWORDS = [
    # (keywords, room_name)
    (["偏好", "习惯", "语言", "风格", "交流", "中文", "英文", "语气"], "user_preferences"),
    (["六部", "A2A", "调度", "朝堂", "尚书", "吏部", "工部", "户部", "礼部", "兵部", "刑部"], "six_ministries"),
    (["记忆", "MemPalace", "ChromaDB", "SQLite", "mempalace", "layer"], "memory_system"),
    (["部署", "docker", "systemctl", "restart", "deploy", "nginx", "cron"], "deployments"),
    (["工具", "skill", "skills", "脚本", "automation", "terminal", "shell"], "tools_skills"),
    (["任务", "todo", "工单", "kanban", "trello", "linear"], "todoist"),
    (["配置", "config", "yaml", "json", "环境变量", "soul", "AGENTS"], "hongjun_config"),
    (["代码", "bug", "feature", "PR", "commit", "git", "算法", "实现"], "general"),
]


def _route_room(content: str, tags: list[str]) -> str:
    """根据内容关键词和标签路由到合适的 room。"""
    text = content.lower()
    text_tags = " ".join(t.lower() for t in tags)

    for keywords, room in _ROOM_KEYWORDS:
        for kw in keywords:
            if kw.lower() in text or kw.lower() in text_tags:
                return room
    return "general"


def _route_hall(content: str, tags: list[str]) -> str:
    """根据内容类型路由到合适的 hall。"""
    text = (content + " " + " ".join(tags)).lower()
    if any(w in text for w in ["偏好", "喜欢", "倾向", "讨厌", "习惯", "讨厌"]):
        return HALL_PREFERENCES
    if any(w in text for w in ["发现", "学到", "学到", "新", "第一次"]):
        return HALL_DISCOVERIES
    if any(w in text for w in ["建议", "应该", "推荐", "最好", "方案"]):
        return HALL_ADVICE
    if any(w in text for w in ["事件", "发生", "做", "完成", "修复", "发布"]):
        return HALL_EVENTS
    return HALL_FACTS


# === L0 动态生成：从 鸿钧 SOUL.md 生成身份 ===

# 源文件在鸿钧项目目录下（不是 Hermes 个人目录）
_SOUL_PATH = "/home/asus/hongjun/SOUL.md"
_AGENTS_PATH = "/home/asus/hongjun/AGENTS.md"

# 正式路径：鸿钧项目目录下
IDENTITY_PATH = "/home/asus/hongjun/data/mempalace_identity.txt"


def generate_l0_identity(identity_path: str) -> str:
    """
    从鸿钧项目 SOUL.md 动态生成 AAAK 风格 L0 身份文件。

    AAAK 格式要求：
      - 每个事实一行：[category] fact
      - ~50-156 tokens（极简）
      - 无连接词，无解释，直接陈述

    输出到 identity_path，供 Layer0.render() 使用。
    """
    # 读取源文件
    soul_content = ""
    agents_content = ""

    if os.path.exists(_SOUL_PATH):
        try:
            with open(_SOUL_PATH, encoding="utf-8") as f:
                soul_content = f.read()
        except Exception:
            pass

    if os.path.exists(_AGENTS_PATH):
        try:
            with open(_AGENTS_PATH, encoding="utf-8") as f:
                agents_content = f.read()
        except Exception:
            pass

    # 如果没有源文件，生成最小身份
    if not soul_content and not agents_content:
        fallback = (
            "## L0 — IDENTITY\n\n"
            "鸿钧：六部尚书协同系统的核心 Agent。\n"
            "六部：吏部/工部/户部/礼部/兵部/刑部。"
        )
        try:
            with open(identity_path, "w", encoding="utf-8") as f:
                f.write(fallback)
        except Exception:
            pass
        return fallback

    # 调用 LLM 生成 AAAK 风格身份
    prompt = f"""你是鸿钧的身份设计师。鸿钧是一个可以自我迭代进化的 Agent。

请从以下源文档提取核心身份信息，生成 AAAK 格式的身份文件。

AAAK 格式规则（给 AI 读的速记，无解码器开销）：
  - 每个事实一行：[category] fact
  - 总长度：50-156 tokens（越短越好）
  - 无连接词，无解释，直接陈述
  - 必须包含：身份定位、六部构成、核心能力、进化方向

Categories 可选：identity / wing / six_ministries / tech_stack / protocol / evolution

---
鸿钧 SOUL.md:
{soul_content[:4000]}
---
鸿钧 AGENTS.md:
{agents_content[:2000]}
---

输出格式示例：
## L0 — IDENTITY

[identity] 鸿钧：独立 AI Agent，内部有六部协作模块
[wing/hongjun] 六部：Coordinator(编排)/Executor(执行)/Memory(记忆)/Tools(工具)/Security(安全)/Evaluator(评测)
[tech_stack] LangGraph|MemPalace|browser-use
[not_shu_liu_bu] 不是外部六部协调系统，不等于中书令

只输出 AAAK 格式的身份内容，不要解释。"""

    llm_content = ""
    try:
        from hongjun.llm import chat_sync, LLMResponse

        resp: LLMResponse = chat_sync(
            messages=[{"role": "user", "content": prompt}],
            model="MiniMax-M2.7",
            temperature=0.3,
            max_tokens=512,
        )
        llm_content = resp.content.strip() if resp.content else ""
    except Exception:
        pass

    # 如果 LLM 失败，用规则生成最小身份
    if not llm_content or len(llm_content) < 50:
        llm_content = (
            "## L0 — IDENTITY\n\n"
            "[identity] 鸿钧：独立 AI Agent\n"
            "[wing/hongjun] 六部：Coordinator/Executor/Memory/Tools/Security/Evaluator\n"
            "[tech_stack] LangGraph|MemPalace|browser-use\n"
            "[not_shu_liu_bu] 不是外部六部协调系统，不等于中书令"
        )

    # 写入文件
    try:
        os.makedirs(os.path.dirname(identity_path), exist_ok=True)
        with open(identity_path, "w", encoding="utf-8") as f:
            f.write(llm_content)
    except Exception:
        pass

    return llm_content


# === L1 LLM 压缩：从 Top Drawers 生成 AAAK 摘要 ===

L1_CACHE_PATH = "/home/asus/hongjun/data/palace/.l1_aaak_cache.txt"


def refresh_l1_with_llm(palace_path: str, top_n: int = 10) -> str:
    """
    从 ChromaDB 取出 top_n 个最高 importance 的 drawers，
    用 LLM 生成 AAAK 压缩摘要，写入 L1 cache 文件。

    供 HongjunMemory 在 wake_up() 时注入。
    返回生成的 L1 AAAK 内容。
    """
    try:
        from mempalace.palace import get_collection

        col = get_collection(palace_path, create=False)
    except Exception:
        return "## L1 — No palace found"

    try:
        all_items = col.get(include=["documents", "metadatas"], limit=1000)
        docs = all_items.get("documents", [])
        metas = all_items.get("metadatas", [])
    except Exception:
        return "## L1 — Cannot access palace"

    if not docs:
        return "## L1 — No memories yet"

    # 按 importance 排序，取 top_n
    scored = []
    for doc, meta in zip(docs, metas):
        imp = meta.get("importance", 0.5)
        try:
            imp = float(imp)
        except (ValueError, TypeError):
            imp = 0.5
        scored.append((imp, meta, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_n]

    # 构建 AAAK 压缩 prompt
    drawers_text = "\n".join(
        f"[{meta.get('room', 'general')}] {doc.strip()[:300]}"
        for _, meta, doc in top
    )

    prompt = f"""你是中书令"鸿钧"的记忆压缩器。请将以下记忆压缩为 AAAK 格式。

AAAK 格式规则：
  - 每个记忆一行：[room] compressed_fact
  - ~120-300 tokens 总长度（极简）
  - 无连接词，直陈事实
  - 按 room 分组，每组不超过3条

---
记忆素材（按重要性排序）：
{drawers_text}
---

输出只有 AAAK 格式内容，格式：
## L1 — ESSENTIAL STORY

[user_preferences] ...
[six_ministries] ...
[memory_system] ...

只输出 AAAK 内容，不要解释。"""

    try:
        from hongjun.llm import chat_sync, LLMResponse

        resp: LLMResponse = chat_sync(
            messages=[{"role": "user", "content": prompt}],
            model="MiniMax-M2.7",
            temperature=0.3,
            max_tokens=1024,
        )
        llm_content = resp.content.strip() if resp.content else ""
    except Exception:
        llm_content = ""

    # 回退：使用内置 L1 generate（规则压缩）
    if not llm_content or len(llm_content) < 30:
        try:
            from mempalace.layers import Layer1

            l1_gen = Layer1(palace_path)
            llm_content = l1_gen.generate()
        except Exception:
            llm_content = "## L1 — Memory loaded"

    # 写入 cache 文件
    try:
        cache_path = L1_CACHE_PATH
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(llm_content)
    except Exception:
        pass

    return llm_content


def read_l1_cache() -> str:
    """读取 L1 cache 文件，不存在则返回空。"""
    try:
        if os.path.exists(L1_CACHE_PATH):
            with open(L1_CACHE_PATH, encoding="utf-8") as f:
                return f.read()
    except Exception:
        pass
    return ""


# === MemPalace CLI 封装 ===

class MemPalaceWrapper:
    """
    MemPalace CLI 封装

    MemPalace 是 CLI 工具，通过 subprocess 调用。
    适合文件类记忆（对话记录、代码片段）。

    文档：https://github.com/milla-jovovich/mempalace
    """

    def __init__(self, palace_path: str = "/home/asus/hongjun/data/palace"):
        self.palace_path = palace_path
        os.makedirs(palace_path, exist_ok=True)

    def _run(self, args: List[str], input_text: Optional[str] = None) -> str:
        """执行 mempalace 命令"""
        cmd = ["mempalace", "--palace", self.palace_path] + args
        try:
            result = subprocess.run(
                cmd,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout or result.stderr or ""
        except subprocess.TimeoutExpired:
            return "⏰ MemPalace 命令超时"
        except FileNotFoundError:
            return "⚠️ mempalace 命令未找到（未安装或不在 PATH）"
        except Exception as e:
            return f"❌ MemPalace 错误: {e}"

    def init(self):
        """初始化 palace"""
        return self._run(["init"])

    def mine(self, content: str, metadata: Optional[Dict] = None):
        """
        挖掘记忆（单条内容）

        用法：
            wrapper.mine("用户问了一个关于 GitHub 的问题", {"type": "question"})
        """
        import json
        meta = metadata or {}
        meta["mined_at"] = datetime.now().isoformat()
        input_json = json.dumps({"content": content, "metadata": meta})
        return self._run(["mine"], input_text=input_json)

    def search(self, query: str) -> List[Dict]:
        """
        搜索记忆

        Returns:
            [{"content": "...", "score": 0.9, ...}]
        """
        result = self._run(["search", query])
        try:
            # MemPalace search 输出是 JSONL
            lines = [l for l in result.strip().split("\n") if l]
            return [json.loads(l) for l in lines]
        except json.JSONDecodeError:
            return [{"content": result, "score": 1.0}]

    def wake_up(self) -> str:
        """获取唤醒上下文（最近最重要的记忆）"""
        return self._run(["wake-up"])

    def status(self) -> str:
        """查看记忆状态"""
        return self._run(["status"])


# === 鸿钧记忆系统（主类）===

class HongjunMemory:
    """
    鸿钧记忆系统 - 基于 MemPalace 4层记忆栈

    四层架构（MemoryStack）：
      L0  身份层   — /home/asus/hongjun/data/mempalace_identity.txt，始终加载（~47 tokens AAAK）
      L1  关键故事 — LLM AAAK 压缩摘要，始终加载（~100 tokens）
      L2  按需召回 — Wing/Room 过滤检索，按话题触发
      L3  深度搜索 — 全量语义搜索，按查询触发

    同时保留 SQLiteMemory 作为降级方案（完全离线，无需 MemPalace）。

    支持方法：
      remember()      — 存入记忆（自动路由到 wing/room）
      recall()        — 检索记忆（L3 语义搜索）
      build_context() — 为 LLM 构建记忆上下文（四层合并）
      wake_up()       — L0 + L1（冷启动）
      refresh_l0()    — 强制刷新 L0 身份（从 SOUL.md / AGENTS.md 重新生成）
      refresh_l1()    — 强制刷新 L1 AAAK 摘要（LLM 重新压缩）
    """

    # 路径：全部在鸿钧项目目录下，不混入 Hermes 个人目录
    PALACE_PATH = "/home/asus/hongjun/data/palace"
    IDENTITY_PATH = "/home/asus/hongjun/data/mempalace_identity.txt"

    def __init__(
        self,
        user_id: str = "default",
        db_path: Optional[str] = None,
        palace_path: Optional[str] = None,
    ):
        self.user_id = user_id

        # SQLite 降级方案路径
        self.db_path = db_path or f"/home/asus/hongjun/data/memory_{user_id}.db"
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.sqlite = SQLiteMemory(self.db_path)

        # MemPalace 路径
        self.palace_path = palace_path or self.PALACE_PATH
        self.identity_path = self.IDENTITY_PATH
        os.makedirs(self.palace_path, exist_ok=True)

        # 初始化 MemoryStack（L0/L1/L2/L3）
        self._stack = None
        self._col = None
        self._mempalace_available = False
        self._init_mempalace()

    def _init_mempalace(self):
        """初始化 MemPalace MemoryStack 和 ChromaDB collection。"""
        try:
            from mempalace.layers import MemoryStack
            from mempalace.palace import get_collection

            self._stack = MemoryStack(
                palace_path=self.palace_path,
                identity_path=self.identity_path,
            )
            self._col = get_collection(self.palace_path, create=True)
            self._mempalace_available = True
        except Exception as e:
            self._mempalace_available = False
            self._stack = None

    def _add_drawer(
        self,
        content: str,
        room: str,
        importance: float = 0.5,
        tags: Optional[List[str]] = None,
        source: str = "memory_system",
    ) -> str:
        """
        添加 drawer 到 ChromaDB（MemPalace 核心存储）。
        返回 drawer_id。
        """
        if not self._mempalace_available:
            return None

        import hashlib
        from datetime import datetime

        try:
            drawer_id = (
                f"drawer_{DEFAULT_WING}_{room}_"
                f"{hashlib.sha256((DEFAULT_WING + room + content).encode()).hexdigest()[:24]}"
            )

            # 去重：已存在则返回现有 ID
            existing = self._col.get(ids=[drawer_id])
            if existing and existing.get("ids"):
                return drawer_id

            hall = _route_hall(content, tags or [])
            self._col.upsert(
                ids=[drawer_id],
                documents=[content],
                metadatas=[{
                    "wing": DEFAULT_WING,
                    "room": room,
                    "hall": hall,
                    "source_file": source,
                    "chunk_index": 0,
                    "added_by": "hongjun",
                    "filed_at": datetime.now().isoformat(),
                    "importance": importance,
                    "tags": json.dumps(tags or []),
                }],
            )
            return drawer_id
        except Exception:
            return None

    def remember(
        self,
        content: str,
        importance: float = 0.5,
        tags: Optional[List[str]] = None,
        use_mempalace: bool = True,
    ) -> str:
        """
        存储记忆：同时写入 SQLite（降级）+ MemPalace ChromaDB（主存储）。

        自动路由到 wing/room：
          - 标签含"偏好"/"习惯" → user_preferences
          - 含"六部"/"A2A"/"调度" → six_ministries
          - 含"部署"/"docker" → deployments
          - 含"记忆"/"MemPalace" → memory_system
          - 含"配置"/"环境变量" → hongjun_config
          - 含"工具"/"skills" → tools_skills
          - 含"任务"/"todo" → todoist
          - 默认 → general
        """
        tags = tags or []
        room = _route_room(content, tags)

        # SQLite 降级（总是写入）
        memory_id = self.sqlite.add(content, importance, tags)

        # MemPalace ChromaDB（主存储）
        if use_mempalace and self._mempalace_available:
            try:
                drawer_id = self._add_drawer(content, room, importance, tags)
                if drawer_id:
                    return drawer_id
            except Exception:
                pass

        # Hindsight（长期记忆，自动四网络分类）
        self._hindsight_retain(content, room, importance, tags)

        return memory_id

    def _hindsight_retain(
        self,
        content: str,
        room: str,
        importance: float,
        tags: list[str] | None,
    ) -> None:
        """后台异步存入 Hindsight（静默失败，不阻塞主流程）。"""
        import threading

        def _do():
            try:
                from hongjun.hindsight_integration import HindsightIntegration
                hi = HindsightIntegration()
                hi.auto_type_retain(content=content, context=room, tags=tags)
            except Exception:
                pass  # 静默，不影响主流程

        t = threading.Thread(target=_do, daemon=True)
        t.start()

    def wake_up(self) -> str:
        """
        冷启动上下文：L0（身份）+ L1（关键记忆）。

        优先使用动态生成的身份（L0）+ LLM 压缩的 L1 cache。
        如 cache 不存在则回退到 MemoryStack 原生实现。
        """
        parts = []

        # L0 — 始终使用动态生成的身份文件
        try:
            l0_render = self._stack.l0.render()
            parts.append(l0_render)
        except Exception:
            parts.append("## L0 — IDENTITY\n鸿钧：中书令。")

        # L1 — 优先使用 LLM 压缩 cache，cache 不存在则调用 LLM 生成
        l1_cache = read_l1_cache()
        if l1_cache and len(l1_cache) > 30:
            parts.append(l1_cache)
        elif self._mempalace_available:
            # 首次或 cache 过期：调用 LLM 生成并 cache
            try:
                l1 = refresh_l1_with_llm(self.palace_path, top_n=10)
                if l1 and len(l1) > 30:
                    parts.append(l1)
                else:
                    parts.append(self._stack.l1.generate())
            except Exception:
                parts.append(self._stack.l1.generate())
        else:
            parts.append("## L1 — No palace")

        return "\n\n".join(parts)

    def refresh_l0(self) -> str:
        """
        强制刷新 L0 身份文件。

        从 SOUL.md / AGENTS.md 重新提取，LLM 生成 AAAK 风格身份，
        写入 identity_path。
        """
        return generate_l0_identity(self.identity_path)

    def refresh_l1(self) -> str:
        """
        强制刷新 L1 AAAK 摘要。

        从 ChromaDB 取 top drawers，LLM 压缩，写入 cache。
        """
        return refresh_l1_with_llm(self.palace_path, top_n=10)

    def recall(self, query: str, top_k: int = 5) -> str:
        """
        检索记忆：L3 深度语义搜索。
        """
        if not self._mempalace_available:
            return ""

        try:
            results = self._stack.l3.search(query, n_results=top_k)
            return results
        except Exception:
            return ""

    def recall_room(self, room: str, n_results: int = 5) -> str:
        """
        按 Room 检索：L2 按需召回。
        """
        if not self._mempalace_available:
            return ""

        try:
            return self._stack.l2.retrieve(room=room, n_results=n_results)
        except Exception:
            return ""

    def get_recent(self, limit: int = 10) -> List[str]:
        """获取最近 N 条记忆（SQLite）。"""
        rows = self.sqlite.get_recent(limit=limit)
        return [r["content"] for r in rows]

    def build_context(self, query: str, max_memories: int = 5) -> str:
        """
        为 LLM 构建记忆上下文。

        三层合并：
          1. wake_up()  — L0 + L1（身份 + 关键记忆，始终注入）
          2. recall()   — L3 深度搜索（当前话题相关记忆）
          3. 最近的 SQLite 记忆（兜底）
        """
        parts = []

        # L0 + L1 — 始终注入
        wake = self.wake_up()
        if wake:
            parts.append(wake)

        # L3 — 语义搜索
        if query:
            search_results = self.recall(query, top_k=max_memories)
            if search_results and "No results" not in search_results:
                parts.append(search_results)

        # SQLite 降级兜底
        if self._mempalace_available:
            recent = self.get_recent(limit=3)
            if recent:
                fallback = "## Recent (SQLite fallback)\n" + "\n".join(f"  - {r}" for r in recent)
                parts.append(fallback)

        if not parts:
            return ""

        return "\n\n" + "\n\n".join(parts) + "\n"

    def status(self) -> str:
        """查看记忆系统状态。"""
        lines = [f"用户: {self.user_id}"]

        if self._mempalace_available:
            try:
                col = self._col
                count = col.count() if col else 0
                lines.append(f"MemPalace ChromaDB: ✅ {count} drawers")
                lines.append(f"  palace: {self.palace_path}")
                lines.append(f"  identity: {self.identity_path}")

                # L0 状态
                l0_tokens = self._stack.l0.token_estimate()
                lines.append(f"  L0 (identity): ~{l0_tokens} tokens")

                # L1 摘要
                l1 = self._stack.l1.generate()
                if l1:
                    lines.append(f"  L1 (essential): {len(l1)} chars")
            except Exception as e:
                lines.append(f"MemPalace: ⚠️ {e}")
        else:
            lines.append("MemPalace: ⚠️ 不可用（已降级至 SQLite）")

        # SQLite 降级
        try:
            recent = self.sqlite.get_recent(limit=3)
            lines.append(f"SQLite: ✅ {len(recent)} recent (降级兜底)")
        except Exception as e:
            lines.append(f"SQLite: ❌ {e}")

        return "\n".join(lines)


# === 单元测试 ===
if __name__ == "__main__":
    mem = HongjunMemory(user_id="test_user")

    print("=" * 50)
    print("鸿钧 · 户部记忆系统测试")
    print("=" * 50)

    # 写入测试
    mid1 = mem.remember("用户喜欢用中文交流", importance=0.9, tags=["偏好", "语言"])
    mid2 = mem.remember("用户昨晚问了关于 GitHub trending 的问题", importance=0.7, tags=["工作"])
    print(f"✓ 记忆写入成功: {mid1}, {mid2}")

    # 检索测试
    context = mem.build_context("用户有什么偏好")
    print(f"✓ 记忆检索:\n{context}")

    # 最近记忆
    recent = mem.get_recent(limit=3)
    print(f"✓ 最近记忆: {recent}")

    # 状态
    print(f"\n✓ 记忆系统状态:\n{mem.status()}")
