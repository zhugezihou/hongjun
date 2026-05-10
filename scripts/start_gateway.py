#!/usr/bin/env python3
"""
鸿钧 Gateway 启动脚本
"""
import sys
import asyncio

sys.path.insert(0, "/home/asus/hongjun/src")

# 结构化日志（惰性初始化，由 hongjun.logging_config 管理）
from hongjun.logging_config import get_logger
logger = get_logger(__name__)


async def main():
    from hongjun.gateway.server import HongjunGateway
    logger.info("启动鸿钧 Gateway", port=20830)
    gw = HongjunGateway(port=20830)
    await gw.start()
    await asyncio.Event().wait()

asyncio.run(main())
