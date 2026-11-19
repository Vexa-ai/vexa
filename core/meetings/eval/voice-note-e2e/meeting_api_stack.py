"""meeting-api for the voice-note E2E (#1783): the shipped app over a real Redis (the voice-note
writer, the bot's own ACL user) with an in-memory meeting store, whose runtime starts the bot as a
local Node process (no browser, no container). Run from the meeting-api project:

    uv run --group production python <this> --port P --verify-key PUB --bot-dir DIR
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid


class LocalBotRuntime:
    """runtime.v1, the three verbs meeting-api calls: the workload is `pnpm tsx src/index.ts` with the
    spec's env, so the bot is the branch's own source."""

    def __init__(self, bot_dir: str, log_dir: str) -> None:
        self.bot_dir, self.log_dir, self.procs = bot_dir, log_dir, {}

    async def create_workload(self, spec: dict) -> dict:
        wid = spec.get("workloadId") or f"local-{uuid.uuid4().hex[:8]}"
        env = {**os.environ, **spec.get("env", {})}
        log = open(os.path.join(self.log_dir, f"{wid}.log"), "w")
        self.procs[wid] = subprocess.Popen(["npx", "tsx", "src/index.ts"], cwd=self.bot_dir, env=env,
                                           stdout=log, stderr=subprocess.STDOUT)
        return {"workloadId": wid, "state": "running"}

    async def delete_workload(self, workload_id: str) -> None:
        p = self.procs.get(workload_id)
        if p and p.poll() is None:
            p.terminate()

    async def get_workload(self, workload_id: str):
        p = self.procs.get(workload_id)
        if p is None:
            return None
        return {"workloadId": workload_id, "state": "running" if p.poll() is None else "stopped"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--verify-key", required=True, help="the gateway's Ed25519 public key (PEM file)")
    ap.add_argument("--bot-dir", required=True)
    ap.add_argument("--log-dir", required=True)
    a = ap.parse_args()

    import redis.asyncio as aioredis
    import uvicorn

    from meeting_api import create_app, identity_token
    from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
    from meeting_api.bot_spawn.workload_redis import BotRedisUsers
    from meeting_api.voice_notes import RedisAudioStreamWriter

    os.environ["MEETING_API_URL"] = f"http://127.0.0.1:{a.port}"
    os.environ["REDIS_URL"] = os.environ["E2E_REDIS_URL"]
    r = aioredis.from_url(os.environ["E2E_REDIS_URL"], decode_responses=True)
    secret = os.environ["ADMIN_TOKEN"]
    rt = LocalBotRuntime(a.bot_dir, a.log_dir)
    app = create_app(
        meeting_repo=InMemoryMeetingRepo(),
        runtime=rt,
        token_secret=secret,
        identity_key=identity_token.read_verify_key(a.verify_key),
        internal_secret=os.environ.get("INTERNAL_API_SECRET", ""),
        bot_redis=BotRedisUsers(r, os.environ["E2E_REDIS_URL"], secret=secret, max_age_sec=6 * 3600),
        voice_note_writer=RedisAudioStreamWriter(r),
        runtime_callback_token="e2e-runtime",
    )
    def _stop_bots() -> None:   # the bots are this process's children: they go with it
        for p in rt.procs.values():
            if p.poll() is None:
                p.terminate()

    try:
        uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
    finally:
        _stop_bots()
    return 0


if __name__ == "__main__":
    sys.exit(main())
