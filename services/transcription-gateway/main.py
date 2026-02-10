"""
Transcription Gateway entrypoint. Runs the WebSocket server from the gateway package.
"""
import asyncio
import logging

from gateway import main as gateway_main
from gateway.settings import LOG_LEVEL

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

if __name__ == "__main__":
    asyncio.run(gateway_main())
