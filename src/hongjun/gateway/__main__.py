"""Allow: python3 -m hongjun.gateway"""
import sys
import asyncio
sys.path.insert(0, '/home/asus/hongjun/src')
from hongjun.logging_config import get_logger
logger = get_logger(__name__)

async def main():
    from hongjun.gateway.server import HongjunGateway
    g = HongjunGateway(port=20830)
    await g.start()
    await asyncio.Event().wait()

asyncio.run(main())
