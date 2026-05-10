"""
鸿钧 · Gateway

HTTP Gateway (port 20830)：
- Session 管理（NEW → ACTIVE → IDLE → COMPRESSING → DONE）
- 请求路由到吏部（Coordinator）
- 并发控制（max 4 并发）
"""

from .server import app, start_gateway
from .session import Session, SessionState
from .db import HongjunDB

__all__ = ["app", "start_gateway", "Session", "SessionState", "HongjunDB"]
