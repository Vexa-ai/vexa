# mock — the offline instrument variant (Lane A)

Mirrors `services/bot/mock/`'s pattern: swaps ONLY the two platform-heavy ports for canned fakes — the Discord voice join + PCM receive (`dave_voice`/py-cord), and the transcription result (no live Whisper worker) — and reuses every REAL adapter (`discord_bot.adapters.*`) unchanged. So this proves the control plane (lifecycle.v1 → meeting-api, transcript.v1 → redis → collector, acts.v1 ← redis) with no live Discord gateway, no DAVE handshake, no GPU/STT worker.

`main.py`'s `run_mock(...)` drives the SAME `discord_bot.session.MeetingSession` orchestrator the real bot uses (`discord_bot.bot`) — only the join + PCM feed + transcribe function are canned. `python -m discord_bot.mock` is the container entrypoint (`Dockerfile.mock`, swapped in as `DISCORD_BOT_IMAGE` for an offline control-plane proof, matching `MOCK_BOT=1` / `BROWSER_IMAGE=mock-bot:dev` for the TS bot).
