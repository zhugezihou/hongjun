"""
鸿钧 · 配置管理
================

单一配置入口，基于 Pydantic Settings。
- 环境变量优先（`HONGJUN_LLM__API_KEY` 覆盖 YAML）
- YAML 提供默认值
- 支持敏感字段脱敏（__repr__ 隐藏）
- 全局单例 `get_settings()`

使用方式：
  from hongjun.config import get_settings
  settings = get_settings()
  api_key = settings.llm.api_key        # 正确获取（或 raise）
  tavily = settings.tools.tavily_api_key  # 同上
  port = settings.server.port            # 有默认值
"""

from __future__ import annotations

import os
import sys
import logging
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("hongjun.config")

# ── 路径常量 ────────────────────────────────────────────────────────

_HONGJUN_ROOT = Path(__file__).parent.parent.parent  # /home/asus/hongjun
_CONFIG_YAML = _HONGJUN_ROOT / "config" / "hongjun.yaml"

# 兼容旧路径（~/.config/hongjun/config.yaml → ~/.hermes/config.yaml）
_HOME_CONFIG_PRIMARY = Path.home() / ".config" / "hongjun" / "config.yaml"
_HOME_CONFIG_FALLBACK = Path.home() / ".hermes" / "config.yaml"


# ── Pydantic 模型 ───────────────────────────────────────────────────

class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o"
    temperature: float = 0.3
    max_tokens: int = 4096
    api_key: Optional[str] = None  # 优先从 env 读取


class A2APort(BaseModel):
    """A2A 端口分配"""
    吏部: int = 20020
    工部: int = 20021
    户部: int = 20022
    礼部: int = 20023
    兵部: int = 20024
    刑部: int = 20025


class A2AConfig(BaseModel):
    ports: dict[str, int] = Field(default_factory=dict)
    heartbeat_interval: int = 30

    @field_validator("ports", mode="before")
    @classmethod
    def _parse_ports(cls, v):
        if isinstance(v, dict):
            return v
        return {}


class ExecutorConfig(BaseModel):
    timeout_seconds: int = 60
    max_retries: int = 2
    allowed_commands: list[str] = Field(default_factory=lambda: ["ls", "cat", "grep", "python", "git", "curl"])
    blocked_commands: list[str] = Field(default_factory=lambda: ["rm -rf /", "mkfs", ":(){ :|:& };:", "dd"])
    sandbox_path: str = "/home/asus/hongjun/sandbox"


class MemoryConfig(BaseModel):
    enabled: bool = True
    backend: str = "sqlite"
    palace_path: str = "/home/asus/hongjun/data/palace"
    db_path: str = "/home/asus/hongjun/data/memory.db"
    max_memories: int = 1000
    importance_threshold: float = 0.3


class ToolsRegistryEntry(BaseModel):
    name: str
    enabled: bool = True
    headless: Optional[bool] = None
    provider: Optional[str] = None
    allowed_paths: Optional[list[str]] = None


class ToolsConfig(BaseModel):
    enabled: bool = True
    registry: list[ToolsRegistryEntry] = Field(default_factory=list)
    tavily_api_key: Optional[str] = None
    jina_api_key: Optional[str] = None


class SecurityInputConfig(BaseModel):
    max_length: int = 50000
    block_patterns: list[str] = Field(default_factory=list)
    block_topics: list[str] = Field(default_factory=list)


class SecurityOutputConfig(BaseModel):
    block_sensitive: bool = True
    max_length: int = 100000


class SecurityConfig(BaseModel):
    enabled: bool = True
    guardrails: str = "nemo"
    nemo_config_path: str = "/home/asus/hongjun/config/guardrails"
    input: SecurityInputConfig = Field(default_factory=SecurityInputConfig)
    output: SecurityOutputConfig = Field(default_factory=SecurityOutputConfig)
    default_permission: str = "USER"


class EvaluationDimension(BaseModel):
    correctness: float = 0.25
    completeness: float = 0.25
    security: float = 0.20
    performance: float = 0.15
    clarity: float = 0.15


class EvaluationConfig(BaseModel):
    enabled: bool = True
    auto_eval: bool = True
    score_threshold: float = 0.7
    dimensions: EvaluationDimension = Field(default_factory=EvaluationDimension)


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 20030
    cors_enabled: bool = False


class DevConfig(BaseModel):
    mock_mode: bool = False
    verbose_logging: bool = False


class HongjunSettings(BaseSettings):
    """
    鸿钧全局配置。

    加载顺序（后面的覆盖前面的）：
      1. config/hongjun.yaml（源码默认）
      2. ~/.config/hongjun/config.yaml（用户覆盖）
      3. ~/.hermes/config.yaml（兼容旧路径）
      4. 环境变量（HONGJUN_* 前缀，永远最高优先）

    环境变量示例：
      HONGJUN_LLM__API_KEY=sk-xxx
      HONGJUN_LLM__PROVIDER=anthropic
      HONGJUN_SERVER__PORT=20031
      HONGJUN_TOOLS__TAVILY_API_KEY=tvly-xxx
      HONGJUN_FEISHU__APP_ID=cli_xxx
      HONGJUN_FEISHU__APP_SECRET=xxx
    """

    model_config = SettingsConfigDict(
        env_prefix="HONGJUN_",
        env_nested_delimiter="__",
        env_parse_enum_str=True,
        extra="ignore",
    )

    # ── 顶层字段 ─────────────────────────────────────────────────
    version: str = "0.1.0"
    name: str = "鸿钧"
    description: str = "六部尚书协同的超强 AI Agent 系统"

    # ── 各部配置（YAML → env 完全对应）─────────────────────────────
    llm: LLMConfig = Field(default_factory=LLMConfig)
    log_level: str = "INFO"

    a2a: A2AConfig = Field(default_factory=A2AConfig)
    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    dev: DevConfig = Field(default_factory=DevConfig)

    # ── 飞书配置（从 feishu_client.py 迁移）───────────────────────
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None

    # ── 内部状态 ─────────────────────────────────────────────────
    _yaml_defaults: dict = {}
    _yaml_override: dict = {}

    # ── 敏感字段 repr ────────────────────────────────────────────
    def __repr__(self) -> str:
        # 隐藏敏感字段
        parts = []
        for field_name in self.model_fields:
            val = getattr(self, field_name)
            if field_name in ("feishu_app_secret", "llm"):
                api_key = getattr(val, "api_key", None) if hasattr(val, "api_key") else None
                if api_key:
                    parts.append(f'{field_name}={{"api_key": "***"}})')
                    continue
            parts.append(f"{field_name}={val!r}")
        return f"HongjunSettings({', '.join(parts)})"


# ── 单例缓存 ────────────────────────────────────────────────────────

_settings: Optional[HongjunSettings] = None


def get_settings(reload: bool = False) -> HongjunSettings:
    """
    返回全局 HongjunSettings 单例。

    首次调用时从 YAML + env 加载。
    reload=True 时强制重新加载（用于测试或热更新）。
    """
    global _settings
    if _settings is not None and not reload:
        return _settings

    _settings = _load_settings()
    return _settings


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个 dict，override 优先级高于 base。"""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _load_yaml_defaults() -> dict:
    """
    合并所有配置源（项目 config > home config > hermes config），
    靠后的优先级更高（覆盖前面的值）。
    """
    yaml_paths = [
        _CONFIG_YAML,
        _HOME_CONFIG_PRIMARY,
        _HOME_CONFIG_FALLBACK,
    ]
    merged: dict = {}
    for path in yaml_paths:
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if data:
                    merged = _deep_merge(merged, data)
                    logger.info(f"配置合并: {path}")
            except Exception as e:
                logger.warning(f"配置读取失败 {path}: {e}")

    if not merged:
        logger.warning(f"未找到配置文件（尝试过: {[str(p) for p in yaml_paths]}）")
    return merged


def _flatten_dict(data: dict, parent_key: str = "", sep: str = "__") -> dict:
    """
    递归将嵌套 dict 展平为单层 dict，key 用 __ 连接。
    遇到 Pydantic model 边界（如 A2AConfig、LLMConfig）时停止展平，
    让 pydantic-settings 直接赋值整个 dict。
    Pydantic model 名（去 _config 后缀）即 top-level 字段名。
    """
    PYDANTIC_MODEL_NAMES = {
        "llm", "a2a", "executor", "memory", "tools",
        "security", "evaluation", "server", "dev",
    }

    items: list[tuple[str, Any]] = []
    for k, v in data.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        # 如果这个 key 对应 Pydantic model，直接保留 dict 不继续展平
        if k in PYDANTIC_MODEL_NAMES and isinstance(v, dict):
            items.append((new_key, v))
        elif isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def _yaml_to_nested_dict(data: dict) -> dict:
    """
    将 YAML dict 转换为 pydantic-settings 兼容的扁平 dict。
    - 跳过顶层 "hongjun" 键（版本/名称等放顶层的配置，在 YAML 里实际没用）
    - 提取 feishu.app_id / feishu.app_secret → feishu_app_id / feishu_app_secret
    - 其余字段（a2a/executor/memory/tools/...）直接展平为 a2a__ports__吏部 格式
    """
    if not data:
        return {}

    result: dict[str, Any] = {}

    # hongjun.* → 直接提升（llm / log_level 等在 hongjun.* 里）
    hj = data.get("hongjun", {})
    for k, v in hj.items():
        if isinstance(v, dict):
            result.update(_flatten_dict({k: v}, sep="__"))
        else:
            result[k] = v

    # feishu.app_id / feishu.app_secret → feishu_app_id / feishu_app_secret
    feishu = data.get("feishu", {}) or {}
    for subkey in ("app_id", "app_secret"):
        if subkey in feishu:
            result[f"feishu_{subkey}"] = feishu[subkey]

    # 其余顶层键（a2a / executor / memory / tools / security / evaluation / server / dev）
    SKIP_KEYS = {"hongjun", "feishu"}
    for key in data:
        if key in SKIP_KEYS:
            continue
        val = data[key]
        if isinstance(val, dict):
            result.update(_flatten_dict({key: val}, sep="__"))
        else:
            result[key] = val

    return result


def _resolve_yaml_env_refs(data: dict) -> dict:
    """
    递归处理 YAML 中的 ${ENV_VAR} 字符串，将其替换为对应环境变量值。
    例如: "${TAVILY_API_KEY}" → "tvly-xxx"（来自环境变量）
    """
    if isinstance(data, dict):
        return {k: _resolve_yaml_env_refs(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_resolve_yaml_env_refs(item) for item in data]
    elif isinstance(data, str):
        # 匹配 ${VAR_NAME} 格式
        import re
        pattern = r'\$\{([^}]+)\}'
        def replacer(m):
            var_name = m.group(1)
            return os.environ.get(var_name, data)  # 未找到则保留原值
        return re.sub(pattern, replacer, data)
    return data


def _load_settings() -> HongjunSettings:
    """
    加载完整配置：YAML（resolve ${ENV_VAR}) + env 覆盖。
    """
    # 1. 读取 YAML
    yaml_data = _load_yaml_defaults()

    # 2. 解析 ${ENV_VAR} 引用（让 YAML 能用环境变量）
    yaml_data = _resolve_yaml_env_refs(yaml_data)

    # 3. 展平嵌套（hongjun.* → 顶层）
    flat = _yaml_to_nested_dict(yaml_data)

    # 4. Pydantic Settings 自动从 env 覆盖，返回合并结果
    # 注意：env 优先级由 BaseSettings 内部处理，flat 作为 kwargs
    settings = HongjunSettings(**flat)
    return settings


# ── 便捷访问器（推荐用法）─────────────────────────────────────────

def get_llm_api_key() -> str:
    """获取 LLM API Key（必须存在，否则 raise）"""
    key = get_settings().llm.api_key
    if not key:
        raise ValueError(
            "LLM API Key 未配置。请设置环境变量 HONGJUN_LLM__API_KEY "
            "或 ~/.config/hongjun/config.yaml 中的 hongjun.llm.api_key"
        )
    return key


def get_tavily_api_key() -> str:
    """获取 Tavily API Key（必须存在，否则 raise）"""
    key = get_settings().tools.tavily_api_key
    if not key:
        raise ValueError(
            "TAVILY_API_KEY 未配置。请设置环境变量 HONGJUN_TOOLS__TAVILY_API_KEY "
            "或 config/hongjun.yaml 中的 hongjun.tools.tavily_api_key"
        )
    return key


def get_feishu_credentials() -> tuple[str, str]:
    """获取飞书 app_id 和 app_secret"""
    s = get_settings()
    app_id = s.feishu_app_id
    app_secret = s.feishu_app_secret
    if not app_id or not app_secret:
        raise ValueError(
            "飞书凭证未配置。请设置 HONGJUN_FEISHU__APP_ID 和 HONGJUN_FEISHU__APP_SECRET"
        )
    return app_id, app_secret
