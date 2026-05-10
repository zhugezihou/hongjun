"""
鸿钧结构化日志配置 — P0-1

特性：
- structlog + stdlib logging 兼容
- Secret redaction（API key / token / password 等）
- 环境变量控制（HONGJUN_LOG_LEVEL, HONGJUN_LOG_JSON）
- 第三方库降噪（uvicorn/httpx/asyncio）
- 预留 OpenTelemetry 桥接路径
"""

import logging
import os
import re
import sys
from typing import Any, Dict

import structlog

# ────────────────────────────────────────────────────────────────
# Secret Redaction — structlog Processor
# ────────────────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    (re.compile(r"(bearer\s+[a-zA-Z0-9\-._~+/+=!@#$%^&*()]+)", re.I), "[REDACTED_BEARER]"),
    (re.compile(r"(sk-[a-zA-Z0-9\-]{20,})"), "[REDACTED_API_KEY]"),
    (re.compile(r'(["\']?(?:api[_\-]?key|token|secret|password|passwd|pwd)["\']?\s*[:=]\s*["\']?)([a-zA-Z0-9\-._~+/!@#$%^&*()+=]{4,})', re.I), r"\1[REDACTED]"),
    (re.compile(r"([A-Z_]{3,20}=(?:bearer|token|key|secret|password)[a-zA-Z0-9\-._~+/]{10,})", re.I), "[REDACTED_ENV]"),
    (re.compile(r"(Basic\s+[a-zA-Z0-9+/=]{15,})"), "[REDACTED_BASIC_AUTH]"),
]


def _redact_secrets(logger: Any, method_name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """递归脱敏 event_dict 中的 secret 值"""
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            for pattern, replacement in _SECRET_PATTERNS:
                value = pattern.sub(replacement, value)
            event_dict[key] = value
        elif isinstance(value, dict):
            event_dict[key] = _redact_secrets(logger, method_name, value)
    return event_dict


# ────────────────────────────────────────────────────────────────
# Logger 惰性配置
# ────────────────────────────────────────────────────────────────

_initialized = False


def _auto_configure() -> None:
    global _initialized
    if _initialized:
        return
    level = os.getenv("HONGJUN_LOG_LEVEL", "INFO").upper()
    json_output = os.getenv("HONGJUN_LOG_JSON", "0") == "1"
    configure_logging(level=level, json_output=json_output)
    _initialized = True


def configure_logging(
    level: str = "INFO",
    json_output: bool = False,
) -> None:
    """配置鸿钧结构化日志。

    Args:
        level: 日志级别，DEBUG/INFO/WARNING/ERROR
        json_output: True=JSON 格式（生产），False=彩色 Console（开发）
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # ── structlog processor 链（用于本项目代码的 logger）─────────
    # 顺序：加时间戳 → 脱敏 → 渲染
    hongjun_processors = [
        structlog.processors.TimeStamper(fmt="iso"),
        _redact_secrets,
    ]

    if json_output:
        hongjun_processors.append(structlog.processors.JSONRenderer())
    else:
        hongjun_processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=hongjun_processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # ── stdlib Handler 配置 ─────────────────────────────────────
    # 本项目代码：structlog logger → ProcessorFormatter → stdlib Handler
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[],  # 第三方库日志：无额外预处理
        processor=(
            structlog.processors.JSONRenderer()
            if json_output
            else structlog.dev.ConsoleRenderer()
        ),
    )

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    stderr_handler.setLevel(numeric_level)

    root = logging.getLogger()
    root.handlers = [stderr_handler]
    root.setLevel(numeric_level)

    # ── 第三方库降噪 ───────────────────────────────────────────
    for lib_name in [
        "uvicorn", "uvicorn.access", "httpx", "httpcore",
        "asyncio", "charset_normalizer", "certifi",
    ]:
        logging.getLogger(lib_name).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取结构化 logger。

    Usage:
        from hongjun.logging_config import get_logger
        log = get_logger(__name__)
        log.info("hello", user_id=123)
    """
    _auto_configure()
    return structlog.get_logger(name)
