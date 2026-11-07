"""The gateway for the call E2E (#1783): the shipped app, signing with a fresh Ed25519 key,
relaying to the local meeting-api with the production `websockets` dialer. One key is accepted (the
identity service is not part of this run). Run from the gateway project:

    uv run --group production python <this> --port P --meeting-api URL --signing-key PEM
"""
from __future__ import annotations

import argparse
import os
import sys


class OneKeyAuthorizer:
    def __init__(self, key: str) -> None:
        self._key = key

    async def resolve(self, api_key: str):
        if api_key != self._key:
            return None
        return {"user_id": 7, "scopes": ["bot", "tx"], "max_concurrent": 3, "email": "e2e@example.test"}

    async def authorize_subscribe(self, api_key: str, meetings: list) -> dict:
        return {"authorized": [], "errors": ["not used in this run"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--meeting-api", required=True)
    ap.add_argument("--signing-key", required=True)
    a = ap.parse_args()

    import httpx
    import redis.asyncio as aioredis
    import uvicorn

    from gateway import create_app
    from gateway.adapters import HttpxDownstreamClient, WebsocketsDialer
    from gateway.identity_token import read_signing_key

    app = create_app(
        OneKeyAuthorizer(os.environ["E2E_API_KEY"]),
        HttpxDownstreamClient(httpx.AsyncClient(timeout=30)),
        aioredis.from_url(os.environ["E2E_REDIS_URL"], decode_responses=True),
        meeting_api_url=a.meeting_api,
        agent_api_url="",
        identity_key=read_signing_key(a.signing_key),
        voice_note_dialer=WebsocketsDialer(),
    )
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
