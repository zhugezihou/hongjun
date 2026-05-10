"""
鸿钧 · 飞书通道

基于 WebSocket 事件订阅，监听飞书群消息。
当收到 @mention 鸿钧的消息时，触发 Gateway 处理并回复。

使用：
  from feishu_client import FeishuClient, start_feishu
  await start_feishu()
"""

import asyncio
import json
import logging
import os
import re
import time
import sys as _sys
import threading
from dataclasses import dataclass
from typing import Optional

import httpx

from hongjun.logging_config import get_logger

logger = get_logger("hongjun.feishu")

# ── 配置加载（迁移到 hongjun.config）───────────────────────────────

def _load_feishu_config() -> tuple[str, str]:
    """
    从 hongjun.config 读取飞书凭证。
    保留函数签名以兼容直接调用，内部委托给 get_feishu_credentials()。
    """
    try:
        from hongjun.config import get_feishu_credentials
        return get_feishu_credentials()
    except ValueError:
        # 回退到环境变量（最终兜底）
        return os.environ.get("FEISHU_APP_ID", ""), os.environ.get("FEISHU_APP_SECRET", "")


# ── 飞书 API ─────────────────────────────────────────────────────

FEISHU_BASE = "https://open.feishu.cn/open-apis"
LARKOO2_BASE = "https://lark-oapi2.feishu.cn/open-apis"  # oapi2 (oauth2)

# Bot 凭证（优先从 ~/.hermes/config.yaml 读取）
_app_id, _app_secret = _load_feishu_config()
APP_ID = _app_id or os.environ.get("FEISHU_APP_ID", "cli_a9334eb4cef85ccd")
APP_SECRET = _app_secret or os.environ.get("FEISHU_APP_SECRET", "")

# 朝堂群
GROUP_CHAT_ID = "oc_d860f9f653e3421db6ea419a81414cf6"


@dataclass
class FeishuMessage:
    message_id: str
    chat_id: str
    user_id: str
    content: str  # 原始 JSON 字符串
    message_type: str  # "text" / "post" / "image" / "audio"
    create_time: str

    @property
    def text_content(self) -> str:
        """提取纯文本内容"""
        if self.message_type == "text":
            try:
                data = json.loads(self.content)
                return data.get("text", "")
            except Exception:
                return self.content
        return self.content


class FeishuClient:
    """
    飞书 API 客户端（HTTP REST 模式）。
    支持：
    - 获取 Bot 信息
    - 获取群成员
    - 发送消息（text / post）
    - 回复消息
    """

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._tenant_access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._bot_open_id: Optional[str] = None  # 缓存 bot open_id
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _ensure_token(self):
        """确保有有效的 tenant_access_token"""
        if (
            self._tenant_access_token
            and time.time() < self._token_expires_at - 60
        ):
            return

        resp = await self._client.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取 token 失败: {data}")
        self._tenant_access_token = data["tenant_access_token"]
        # token 有效期 2 小时
        self._token_expires_at = time.time() + 7200

    async def _request(
        self, method: str, path: str, **kwargs
    ) -> dict:
        """带 token 自动刷新的 HTTP 请求"""
        await self._ensure_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._tenant_access_token}"
        resp = await self._client.request(
            method, f"{FEISHU_BASE}{path}", headers=headers, **kwargs
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(f"飞书 API 错误: {data}")
        return data

    # ── Bot 信息 ───────────────────────────────────────────────

    async def get_bot_info(self) -> dict:
        """获取 Bot 信息，并缓存 open_id"""
        data = await self._request("GET", "/bot/v3/info")
        if data.get("code") == 0:
            self._bot_open_id = data.get("bot", {}).get("open_id")
        return data

    @property
    def bot_open_id(self) -> Optional[str]:
        """返回 bot open_id（需确保已调用过 get_bot_info）"""
        return self._bot_open_id

    # ── 消息读取 ──────────────────────────────────────────────

    async def get_messages(
        self, container_id: str, container_type: str = "chat"
    ) -> list[dict]:
        """获取群消息（分页）"""
        messages = []
        page_token = None
        while True:
            params = {"container_id": container_id, "container_type": container_type, "page_size": 50}
            if page_token:
                params["page_token"] = page_token
            data = await self._request("GET", "/im/v1/messages", params=params)
            items = data.get("data", {}).get("items", [])
            messages.extend(items)
            page_token = data.get("data", {}).get("page_token")
            if not page_token or not items:
                break
        return messages

    async def get_recent_messages(
        self, chat_id: str, limit: int = 20
    ) -> list[dict]:
        """获取最近 N 条消息（正确参数：container_id_type=chat）"""
        all_messages: list[dict] = []
        page_token = None
        while len(all_messages) < limit:
            params = {
                "container_id_type": "chat",
                "container_id": chat_id,
                "page_size": min(limit, 50),
                "sort_type": "ByCreateTimeDesc",
            }
            if page_token:
                params["page_token"] = page_token
            data = await self._request("GET", "/im/v1/messages", params=params)
            items = data.get("data", {}).get("items", [])
            if not items:
                break
            all_messages.extend(items)
            page_token = data.get("data", {}).get("page_token")
            if not page_token:
                break
        return all_messages

    async def get_message_by_id(self, message_id: str) -> dict:
        """获取单条消息详情"""
        return await self._request("GET", f"/im/v1/messages/{message_id}")

    # ── 消息发送 ──────────────────────────────────────────────

    async def send_text(self, receive_id: str, receive_id_type: str, text: str) -> dict:
        """
        发送文本消息。

        注意：receive_id_type 必须在 URL query params 里，不能放在 body。
        参见 https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/create
        """
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }
        # receive_id_type 是 query 参数，不是 body 参数
        return await self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            json=payload,
        )

    async def reply_text(self, message_id: str, text: str) -> dict:
        """回复消息（基于原消息）"""
        payload = {
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }
        return await self._request(
            "POST",
            f"/im/v1/messages/{message_id}/reply",
            json=payload,
        )

    async def send_text_to_chat(self, chat_id: str, text: str) -> dict:
        """发送文本消息到群"""
        return await self.send_text(chat_id, "chat_id", text)

    # ── Webhook 模式（备用）───────────────────────────────────

    def build_webhook_url(self, webhook_token: str) -> str:
        """构建 webhook URL"""
        return f"{LARKOO2_BASE}/im/v1/messages?token={webhook_token}"

    async def close(self):
        await self._client.aclose()


# ── 飞书事件处理器 ────────────────────────────────────────────────

class FeishuHandler:
    """
    飞书消息事件处理。

    策略：
    1. 轮询模式（polling）—— 定期拉取群消息，检查是否有新的 @mention
    2. 事件模式（event）—— 订阅 WebSocket 事件（需要飞书事件订阅配置）

    默认用轮询模式，5 秒一次。
    """

    def __init__(
        self,
        client: FeishuClient,
        gateway_url: str = "http://localhost:20830",
        chat_id: str = GROUP_CHAT_ID,
        poll_interval: float = 5.0,
    ):
        self.client = client
        self.gateway_url = gateway_url
        self.chat_id = chat_id
        self.poll_interval = poll_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # 追踪已处理的消息（避免重复）
        self._seen_message_ids: set[str] = set()
        self._seen_path = "/home/asus/hongjun/db/feishu_seen.json"
        self._load_seen()

    def _load_seen(self):
        """从文件恢复已处理的消息 ID"""
        try:
            with open(self._seen_path) as f:
                data = json.load(f)
                self._seen_message_ids = set(data.get("ids", []))
                logger.info(f"已加载 {len(self._seen_message_ids)} 个已处理消息 ID")
        except Exception:
            pass

    def _save_seen(self):
        """保存已处理消息 ID 到文件"""
        try:
            with open(self._seen_path, "w") as f:
                json.dump(
                    {"ids": list(self._seen_message_ids)[-1000:]},
                    f,
                )
        except Exception:
            pass

    def _mark_seen(self, message_id: str):
        self._seen_message_ids.add(message_id)
        # 最多保留 1000 条
        if len(self._seen_message_ids) > 1000:
            self._seen_message_ids = set(list(self._seen_message_ids)[-1000:])
        self._save_seen()

    def is_seen(self, message_id: str) -> bool:
        return message_id in self._seen_message_ids

    async def _poll(self):
        """轮询群消息"""
        last_time = ""
        while self._running:
            try:
                messages = await self.client.get_recent_messages(self.chat_id, limit=10)
                logger.debug("poll_fetched", count=len(messages), chat_id=self.chat_id)
                for msg in messages:
                    message_id = msg.get("message_id", "")
                    if not message_id or self.is_seen(message_id):
                        continue

                    self._mark_seen(message_id)

                    # 解析消息
                    msg_type = msg.get("msg_type", "")
                    body = msg.get("body", {})
                    content = body.get("content", "{}")

                    # 提取文本
                    text = ""
                    if msg_type == "text":
                        try:
                            text = json.loads(content).get("text", "")
                        except Exception:
                            text = content

                    # 跳过空消息
                    if not text.strip():
                        continue

                    # 检查是否 @ 机器人
                    mentions = msg.get("mentions", [])
                    # 用 mention 的 id（open_id）与 bot 自己的 open_id 比对
                    bot_open_id = self.client.bot_open_id
                    bot_mentioned = any(
                        m.get("id", "") == bot_open_id
                        for m in mentions
                    )
                    # 飞书有时返回占位符 open_id（如 @_user_1），无法直接比对。
                    # 关键：如果 sender 的 open_id 就是 bot 自己的 open_id，
                    # 说明这条消息是 bot 给自己发的（消息内容中包含 @mention 占位符），
                    # 此时走 fallback（len(mentions)>0 视为 @bot）是有道理的。
                    # 但如果 sender 不是 bot，则无法判断 mention 是 @bot 还是 @别人。
                    # 正确逻辑：sender == bot_open_id → 走 fallback；否则 → 只用 open_id 精确匹配
                    sender_open_id = msg.get("sender", {}).get("id", "")
                    is_bot_msg = sender_open_id == bot_open_id
                    if is_bot_msg and not bot_mentioned and len(mentions) > 0:
                        # bot 自己的消息里有 @mention 占位符 → 视为 @bot
                        bot_mentioned = True

                    logger.debug(
                        "poll_msg_received",
                        message_id=message_id[:20],
                        msg_type=msg_type,
                        mentions=len(mentions),
                        text=text[:50],
                        bot_mentioned=bot_mentioned,
                        is_bot=is_bot_msg,
                    )

                    # 如果是机器人发的消息，跳过（避免自己回复自己）
                    if is_bot_msg:
                        continue

                    # 只有 @mention 机器人本身才处理
                    if bot_mentioned:
                        # 去掉 @mention 前缀
                        clean_text = re.sub(r'@\S+', '', text).strip()
                        if clean_text:
                            await self._handle_user_message(clean_text, message_id)

            except Exception as e:
                logger.error(f"轮询错误: {e}")

            await asyncio.sleep(self.poll_interval)

    async def _handle_user_message(self, text: str, in_reply_to: str):
        """将用户消息转发给 Gateway 处理"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                resp = await http.post(
                    f"{self.gateway_url}/chat",
                    json={
                        "message": text,
                        "platform": "feishu",
                        "platform_chat_id": self.chat_id,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    reply_text = data.get("response", "（无响应）")

                    # 回复到群
                    await self.client.reply_text(in_reply_to, reply_text)
                    logger.info(f"已回复: {reply_text[:80]}")
                else:
                    logger.warning(f"Gateway 返回 {resp.status_code}")
        except Exception as e:
            logger.error(f"转发到 Gateway 失败: {e}")
            try:
                await self.client.reply_text(
                    in_reply_to,
                    f"【错误】无法处理: {e}",
                )
            except Exception:
                pass

    async def start(self):
        """启动轮询"""
        self._running = True
        # 验证 bot 连接
        bot_info = await self.client.get_bot_info()
        logger.info(f"飞书 Bot 信息: {bot_info}")
        self._task = asyncio.create_task(self._poll())
        logger.info(f"飞书通道已启动（轮询模式，{self.poll_interval}s）")

    async def stop(self):
        """停止轮询"""
        self._running = False
        if self._task:
            self._task.cancel()
        await self.client.close()
        logger.info("飞书通道已停止")


# ── WebSocket 事件处理器 ─────────────────────────────────────────

class FeishuWebSocketHandler:
    """
    飞书 WebSocket 实时事件处理器（替代轮询）。

    使用 lark-oapi ws.Client 接收飞书实时事件：
    - im.message.receive_v1（收到消息）
    - 自动去重（基于 message_id）
    - 收到 @mention 消息后 POST 到 Gateway /chat
    - Gateway 回复后用 reply_text 回调

    ws.Client 在独立后台线程运行（避免阻塞 asyncio 主循环），
    stop() 通过 threading.Event 信号安全终止。
    """

    def __init__(
        self,
        client: FeishuClient,
        gateway_url: str = "http://localhost:20830",
        chat_id: str = GROUP_CHAT_ID,
    ):
        self.client = client
        self.gateway_url = gateway_url
        self.chat_id = chat_id
        self._ws_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started = False

        # 追踪已处理消息（避免重复）
        self._seen_message_ids: set[str] = set()
        self._seen_path = "/home/asus/hongjun/db/feishu_seen.json"
        self._load_seen()

    def _load_seen(self):
        try:
            with open(self._seen_path) as f:
                data = json.load(f)
                self._seen_message_ids = set(data.get("ids", []))
                logger.info("ws_handler_loaded_seen", count=len(self._seen_message_ids))
        except Exception:
            pass

    def _save_seen(self):
        try:
            with open(self._seen_path, "w") as f:
                json.dump({"ids": list(self._seen_message_ids)[-1000:]}, f)
        except Exception:
            pass

    def _mark_seen(self, message_id: str):
        self._seen_message_ids.add(message_id)
        if len(self._seen_message_ids) > 1000:
            self._seen_message_ids = set(list(self._seen_message_ids)[-1000:])
        self._save_seen()

    def _ws_thread_target(self):
        """
        在独立线程运行 ws.Client（ws.Client.start() 是阻塞的）。
        ws.Client 内部有自己的 asyncio event loop。
        """
        from lark_oapi.ws import Client as WsClient
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

        # 注册消息事件回调
        def on_message_receive_v1(event: P2ImMessageReceiveV1) -> None:
            try:
                msg_obj = event.event
                if msg_obj is None:
                    return

                msg_id = msg_obj.message.message_id or ""
                if not msg_id or msg_id in self._seen_message_ids:
                    return

                self._mark_seen(msg_id)

                # 解析消息内容
                msg_type = msg_obj.message.message_type or ""
                raw_content = msg_obj.message.content or "{}"
                mentions = msg_obj.message.mentions or []

                try:
                    content_obj = json.loads(raw_content)
                except Exception:
                    content_obj = {}

                text = content_obj.get("text", "") if isinstance(content_obj, dict) else str(raw_content)

                # 获取发送者
                sender = msg_obj.sender
                bot_open_id = getattr(self.client, "_bot_open_id", None) or ""
                sender_open_id = ""
                if sender and sender.sender_id:
                    sender_open_id = sender.sender_id.open_id or ""

                is_bot_msg = sender_open_id == bot_open_id

                # 检查 @mention
                bot_mentioned = any(
                    (m.mention_key or "").strip() != ""
                    for m in mentions
                ) or any(
                    getattr(m, "id", "") == bot_open_id
                    for m in mentions
                )

                # bot 自己的消息特殊处理
                if is_bot_msg and not bot_mentioned and len(mentions) > 0:
                    bot_mentioned = True

                logger.debug(
                    "ws_msg_received",
                    message_id=msg_id[:20],
                    msg_type=msg_type,
                    mentions=len(mentions),
                    text=text[:50],
                    bot_mentioned=bot_mentioned,
                    is_bot=is_bot_msg,
                    chat_id=msg_obj.message.chat_id,
                )

                if is_bot_msg:
                    return

                if not text.strip():
                    return

                if not bot_mentioned:
                    return

                # 去掉 @mention 前缀
                clean_text = re.sub(r"@\S+", "", text).strip()
                if not clean_text:
                    return

                # 异步转发到 gateway（在主 asyncio loop 执行）
                asyncio.run(self._forward_to_gateway(clean_text, msg_id, msg_obj.message.chat_id))

            except Exception as e:
                logger.error("ws_msg_process_error", error=str(e))

        try:
            handler = (
                EventDispatcherHandler.builder("", "")  # ws 模式不需要 encrypt/verification
                .register_p2_im_message_receive_v1(on_message_receive_v1)
                .build()
            )

            ws_client = WsClient(
                app_id=APP_ID,
                app_secret=APP_SECRET,
                event_handler=handler,
                auto_reconnect=True,
            )

            self._ws_client = ws_client
            logger.info("ws_client_connecting")
            ws_client.start()
        except Exception as e:
            logger.error("ws_client_error", error=str(e))

    async def _forward_to_gateway(self, text: str, in_reply_to: str, chat_id: str):
        """将用户消息转发给 Gateway 处理（异步）"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                resp = await http.post(
                    f"{self.gateway_url}/chat",
                    json={
                        "message": text,
                        "platform": "feishu",
                        "platform_chat_id": chat_id,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    reply_text = data.get("response", "（无响应）")
                    await self.client.reply_text(in_reply_to, reply_text)
                    logger.info("ws_replied", text=reply_text[:80])
                else:
                    logger.warning("ws_gateway_error", status=resp.status_code)
        except Exception as e:
            logger.error("ws_forward_error", error=str(e))

    async def start(self):
        """启动 WebSocket 客户端（在后台线程）"""
        # 先获取 bot open_id（用于 @mention 判断）
        await self.client.get_bot_info()
        logger.info("feishu_ws_starting")

        self._stop_event.clear()
        self._ws_thread = threading.Thread(target=self._ws_thread_target, daemon=True)
        self._ws_thread.start()
        self._started = True
        logger.info("feishu_ws_started")

    async def stop(self):
        """安全停止 WebSocket 客户端"""
        if not self._started:
            return

        logger.info("feishu_ws_stopping")
        self._stop_event.set()

        # 关闭 websocket 连接，触发 ws_client.start() 的 loop.run_until_complete 返回
        if hasattr(self, "_ws_client") and self._ws_client:
            ws_conn = getattr(self._ws_client, "_conn", None)
            if ws_conn:
                try:
                    await ws_conn.close()
                except Exception:
                    pass

        if self._ws_thread:
            self._ws_thread.join(timeout=10)
            self._ws_thread = None

        await self.client.close()
        logger.info("feishu_ws_stopped")


# ── 全局实例 ──────────────────────────────────────────────────────

_client: Optional[FeishuClient] = None
_handler: Optional = None  # Union[FeishuHandler, FeishuWebSocketHandler]


async def start_feishu(
    gateway_url: str = "http://localhost:20830",
    mode: str = "ws",
) -> FeishuWebSocketHandler | FeishuHandler:
    """
    启动飞书通道。

    Args:
        gateway_url: Gateway HTTP 地址
        mode: "ws"（WebSocket 实时事件，默认）或 "poll"（5s 轮询，兼容旧逻辑）
    """
    global _client, _handler
    _client = FeishuClient(APP_ID, APP_SECRET)

    if mode == "ws":
        _handler = FeishuWebSocketHandler(
            client=_client,
            gateway_url=gateway_url,
        )
    else:
        # 轮询模式（兼容）
        _handler = FeishuHandler(
            client=_client,
            gateway_url=gateway_url,
        )

    await _handler.start()
    return _handler


async def stop_feishu():
    """停止飞书通道"""
    global _handler
    if _handler:
        await _handler.stop()
