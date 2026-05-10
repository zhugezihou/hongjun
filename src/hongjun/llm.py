"""
鸿钧 · LLM 集成层

统一 LLM 调用接口，支持多 provider：
- MiniMax（复用 Hermes 的 Api.minimaxi.com）
- OpenAI (GPT-4o)
- DeepSeek

使用方式：
  from llm import chat, get_model
  response = await chat("写一个快排算法", model="minimax")
"""

import os
import json
import logging
import time
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

import httpx

from hongjun.config import get_settings

logger = logging.getLogger("hongjun.llm")

# ── 常量 ──────────────────────────────────────────────────────────

MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"

# 保留旧常量作为向后兼容（首次访问时从 settings 懒加载）
_LAZY_MAPPINGS = {}


def __getattr__(name: str):
    if name == "MINIMAX_API_KEY":
        return get_settings().llm.api_key or os.environ.get("MINIMAX_API_KEY", "")
    if name == "MINIMAX_MODEL":
        return get_settings().llm.model
    if name == "MINIMAX_PROVIDER":
        return get_settings().llm.provider
    if name == "_llm_cfg":
        return get_settings().llm.model_dump()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: dict  # {"prompt_tokens": N, "completion_tokens": N, "total": N}
    latency_s: float
    raw: dict  # 原始响应


# ── Provider 实现 ─────────────────────────────────────────────────

async def _call_minimax(
    messages: list[dict],
    model: str = "MiniMax-M2.7",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    timeout: float = 60.0,
) -> LLMResponse:
    """调用 MiniMax API（与 Hermes 相同端点）"""
    api_key = get_settings().llm.api_key or os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        raise ValueError("MINIMAX_API_KEY not configured — 请在 ~/.config/hongjun/config.yaml 中配置 llm.api_key")

    url = f"{MINIMAX_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    start = time.time()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    latency = time.time() - start
    choice = data["choices"][0]
    content = choice["message"]["content"]

    return LLMResponse(
        content=content,
        model=data.get("model", model),
        usage=data.get("usage", {}),
        latency_s=latency,
        raw=data,
    )


async def _call_openai(
    messages: list[dict],
    model: str = "gpt-4o",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    timeout: float = 60.0,
) -> LLMResponse:
    """调用 OpenAI API"""
    api_key = get_settings().llm.api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not configured — 请在 ~/.config/hongjun/config.yaml 中配置 llm.api_key")

    url = f"{MINIMAX_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    start = time.time()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    latency = time.time() - start
    choice = data["choices"][0]
    content = choice["message"]["content"]

    return LLMResponse(
        content=content,
        model=data.get("model", model),
        usage=data.get("usage", {}),
        latency_s=latency,
        raw=data,
    )


# ── Streaming LLM ──────────────────────────────────────────────────────

async def _stream_minimax(
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
    chunk_size: int,
    chunk_interval: float,
) -> dict:
    """MiniMax 流式实现（yield 事件供外部迭代）"""
    api_key = get_settings().llm.api_key or os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        yield {"type": "error", "content": "MINIMAX_API_KEY not configured"}
        return

    url = f"{MINIMAX_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64: x64; rv:148.0) Gecko/20100101 Firefox/148.0",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }

    accumulated = ""
    last_yield_time = time.time()
    usage = {}

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                stripped = line.strip()
                if not stripped or stripped == "[DONE]":
                    if stripped == "[DONE]":
                        break
                    continue
                if not stripped.startswith("data: "):
                    continue
                data_str = stripped[6:].strip()
                if not data_str:
                    continue
                try:
                    chunk_data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                delta = (
                    chunk_data.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content", "")
                )
                if not delta:
                    delta = (
                        chunk_data.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("text", "")
                    )

                if delta:
                    accumulated += delta
                    now = time.time()
                    should_yield = (
                        (chunk_size > 0 and len(accumulated) >= chunk_size)
                        or (chunk_size == 0 and now - last_yield_time >= chunk_interval)
                    )
                    if should_yield:
                        yield {"type": "chunk", "content": accumulated}
                        accumulated = ""
                        last_yield_time = now

                    usage = chunk_data.get("usage", {})

            if accumulated:
                yield {"type": "chunk", "content": accumulated}

    yield {"type": "done", "content": "", "usage": usage, "model": model}


async def _stream_openai(
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
    chunk_size: int,
    chunk_interval: float,
) -> dict:
    """OpenAI 兼容流式实现（yield 事件供外部迭代）"""
    api_key = get_settings().llm.api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        yield {"type": "error", "content": "OPENAI_API_KEY not configured"}
        return

    url = f"{MINIMAX_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }

    accumulated = ""
    last_yield_time = time.time()
    usage = {}

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                stripped = line.strip()
                if not stripped or stripped == "[DONE]":
                    if stripped == "[DONE]":
                        break
                    continue
                if not stripped.startswith("data: "):
                    continue
                data_str = stripped[6:].strip()
                if not data_str:
                    continue
                try:
                    chunk_data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                delta = (
                    chunk_data.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content", "")
                )

                if delta:
                    accumulated += delta
                    now = time.time()
                    should_yield = (
                        (chunk_size > 0 and len(accumulated) >= chunk_size)
                        or (chunk_size == 0 and now - last_yield_time >= chunk_interval)
                    )
                    if should_yield:
                        yield {"type": "chunk", "content": accumulated}
                        accumulated = ""
                        last_yield_time = now

                    usage = chunk_data.get("usage", {})

            if accumulated:
                yield {"type": "chunk", "content": accumulated}

    yield {"type": "done", "content": "", "usage": usage, "model": model}


# Provider 分发表（与 PROVIDERS 对应）
STREAM_PROVIDERS = {
    "minimax": _stream_minimax,
    "openai": _stream_openai,
}


async def stream(
    messages: list[dict],
    model: str = "MiniMax-M2.7",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    timeout: float = 60.0,
    chunk_size: int = 20,  # yield every N chars
    chunk_interval: float = 0.1,  # or every N seconds
) -> dict:
    """
    流式 LLM 调用 —— 通过 SSE 实时推送文本片段。

    返回格式（每个 chunk 为 dict）：
      {"type": "chunk",  "content": "正在分析..."}
      {"type": "done",    "content": "", "usage": {...}}
      {"type": "error",  "content": "错误信息"}

    chunk_size=0 时禁用字符触发，改为定时触发（适合实时显示思考过程）。
    自动根据 model 名推断 provider 并分发。
    """
    provider_name, model_id = resolve_provider(model)
    stream_fn = STREAM_PROVIDERS.get(provider_name)
    if not stream_fn:
        yield {"type": "error", "content": f"No stream provider for {provider_name}"}
        return

    async for event in stream_fn(
        messages=messages,
        model=model_id,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        chunk_size=chunk_size,
        chunk_interval=chunk_interval,
    ):
        yield event

PROVIDERS = {
    "minimax": _call_minimax,
    "openai": _call_openai,
}


def resolve_provider(model: str) -> tuple[str, str]:
    """
    从模型名推断 provider。
    返回 (provider_name, model_id)。
    """
    model = model.lower()
    if "minimax" in model or "m2" in model or "mimo" in model:
        return "minimax", model
    elif "gpt" in model or "openai" in model:
        return "openai", model
    elif "deepseek" in model:
        return "openai", "deepseek-chat"  # DeepSeek 兼容 OpenAI 格式
    else:
        # 默认 MiniMax
        return "minimax", "MiniMax-M2.7"


async def chat(
    messages: list[dict],
    model: str = "MiniMax-M2.7",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    timeout: float = 60.0,
) -> LLMResponse:
    """
    统一的 LLM 调用接口。

    messages 格式：
      [{"role": "system", "content": "..."},
       {"role": "user", "content": "..."}]

    返回 LLMResponse。
    """
    provider_name, model_id = resolve_provider(model)
    provider_fn = PROVIDERS.get(provider_name)

    if not provider_fn:
        raise ValueError(f"Unknown provider: {provider_name}")

    logger.info(f"LLM call: provider={provider_name} model={model_id}")
    return await provider_fn(
        messages=messages,
        model=model_id,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )


async def chat_simple(
    prompt: str,
    system: str = "你是鸿钧，一个智能助手。",
    model: str = "MiniMax-M2.7",
    **kwargs,
) -> str:
    """简化接口：输入字符串，直接返回字符串。"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    resp = await chat(messages, model=model, **kwargs)
    return resp.content


# ── 同步 LLM 调用（供 orchestrator 等同步上下文使用）────────────────────


def chat_sync(
    messages: list[dict],
    model: str = "MiniMax-M2.7",
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: float = 60.0,
) -> LLMResponse:
    """
    同步 LLM 调用（内部使用 httpx blocking 请求）。

    用于 orchestrator 等无法使用 async 的同步上下文。
    """
    api_key = get_settings().llm.api_key or os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        return LLMResponse(content="", model=model, usage={}, latency_s=0, raw={})

    url = f"{MINIMAX_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    start = time.time()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    latency = time.time() - start
    choice = data["choices"][0]
    content = choice["message"]["content"]

    return LLMResponse(
        content=content,
        model=data.get("model", model),
        usage=data.get("usage", {}),
        latency_s=latency,
        raw=data,
    )

# ── LLM Model Wrapper（供 FunctionCallAgent 使用）───────────────────────────


class MiniMaxChatModel:
    """
    同步 LLM 模型封装，提供 agent 需要的标准接口：

      model.chat(messages, functions=None, stream=False) -> LLMResponse / Iterator[LLMResponse]

    支持 function calling（tools 参数）和流式输出。
    """

    def __init__(
        self,
        model: str = "MiniMax-M2.7",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def chat(
        self,
        messages: list[dict],
        functions: Optional[list[dict]] = None,
        stream: bool = False,
        **kwargs,
    ):
        """同步 chat 接口（stream=True 时返回 generator）"""
        if stream:
            return self._stream(messages, functions, **kwargs)
        return self._blocking(messages, functions, **kwargs)

    def _blocking(
        self,
        messages: list[dict],
        functions: Optional[list[dict]] = None,
        **kwargs,
    ) -> "LLMResponse":
        api_key = get_settings().llm.api_key or os.environ.get("MINIMAX_API_KEY", "")
        if not api_key:
            return LLMResponse(content="【错误】MINIMAX_API_KEY 未配置", model=self.model, usage={}, latency_s=0, raw={})

        url = f"{MINIMAX_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
        }
        if functions:
            # functions 已经是完整的 OpenAI tools 格式：{"type": "function", "function": {...}}
            # 不需要再包装一层
            payload["tools"] = functions
            if kwargs.get("tool_choice"):
                payload["tool_choice"] = kwargs["tool_choice"]

        start = time.time()
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        latency = time.time() - start
        choice = data["choices"][0]

        content = choice["message"].get("content") or ""
        function_call = None
        tool_calls = choice["message"].get("tool_calls") or []
        if tool_calls:
            tc = tool_calls[0]
            function_call = {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}

        result = LLMResponse(
            content=content,
            model=data.get("model", self.model),
            usage=data.get("usage", {}),
            latency_s=latency,
            raw=data,
        )
        result.function_call = function_call
        return result

    def _stream(self, messages: list[dict], functions: Optional[list[dict]] = None, **kwargs):
        """流式 chat（同步 generator）"""
        api_key = get_settings().llm.api_key or os.environ.get("MINIMAX_API_KEY", "")
        if not api_key:
            return

        url = f"{MINIMAX_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "stream": True,
        }
        if functions:
            payload["tools"] = functions  # already in OpenAI format

        with httpx.Client(timeout=self.timeout, headers={"Accept": "text/event-stream"}) as client:
            with client.stream("POST", url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                accumulated = ""
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            import json as _json
                            chunk = _json.loads(data_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            token = delta.get("content", "")
                            if token:
                                accumulated += token
                                yield LLMResponse(
                                    content=accumulated,
                                    model=self.model,
                                    usage={},
                                    latency_s=0,
                                    raw=chunk,
                                )
                        except _json.JSONDecodeError:
                            continue


def get_chat_model(config: dict) -> "MiniMaxChatModel":
    """从配置 dict 创建 LLM 模型实例（兼容 FunctionCallAgent）"""
    return MiniMaxChatModel(
        model=config.get("model", "MiniMax-M2.7"),
        temperature=config.get("temperature", 0.3),
        max_tokens=config.get("max_tokens", 4096),
        timeout=config.get("timeout", 60.0),
    )

