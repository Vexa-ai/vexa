# deploy/lite/tests/e2e — the synthetic capture→archive E2E rig (WP-M9 C)

**Concern:** the ONE lane that exercises the Minutes engine end-to-end per PR — control-plane
capture → runtime spawn → lifecycle FSM → `transcription_segments` stream → collector →
`transcriptions` rows → stop → settlement callbacks → summarizer → read index. It exists because a
live session found nine stacked defects on exactly this path, each invisible behind the previous:
nothing between the unit suites and a human in a real meeting ever walked capture→archive.

**Files:**
- `run_e2e.sh` — the driver + assertions, staged and loud (the failing stage is named). Runs against
  an already-booted lite stack (pr-value `lite-smoke` boots it; locally `make -C deploy/lite up`).
- `scripted_bot.py` — the deterministic bot the runtime spawns via its documented `BOT_COMMAND`
  override. Real invocation in `VEXA_BOT_CONFIG`, real lifecycle callbacks, real segment stream,
  real leave command; only the browser/audio is absent. Scenario rides the meeting URL
  (`…-lobb-…` = lobby-only, else normal-active).
- `stub_llm.py` — the loopback OpenAI-compatible backend (chat completions + transcriptions faces)
  and the Minutes-Hub callback sink (sealed `CallbackAck`, events replayed on `GET /_events`).
  `SUMMARY_SERVICE_URL` / `TRANSCRIPTION_SERVICE_URL` / `MINUTES_ENGINE_CALLBACK_URL` point at it
  through their EXISTING declared config keys — the rig adds no config.v1 surface. The rig also
  sets `SUMMARY_SERVICE_TOKEN` (declared, defaulted) to a non-empty value: the summarizer client
  sends `Authorization` unconditionally, and an empty token yields the header value `Bearer `
  which httpx refuses — the stub itself ignores the token.

**Settlement stages carry WORKSTREAM A's contract** (metering settles true ACTIVE seconds; a
lobby-only capture settles 0). `settlement-lobby` is EXPECTED RED against pre-A engines — that is
the recorded defect evidence, not a rig bug. `E2E_SETTLEMENT_STRICT=0` downgrades only the
settlement stages to warnings.

**Local run:**
```
make -C deploy/lite build && make -C deploy/lite up   # or let it pull the Hub image
deploy/lite/tests/e2e/run_e2e.sh
```

**Instrument fidelity** is pinned in the meeting-api suite
(`core/meetings/services/meeting-api/tests/test_e2e_rig_instruments.py`): the scripted bot's
lifecycle events validate against sealed `lifecycle.v1`, its segment envelope drives the real
collector `ingest`, the stub's completion satisfies the real `openai_chat_llm` client under the
exact `SUMMARY_SERVICE_TOKEN` value `run_e2e.sh` exports (read out of the script, so pin and live
wiring share one axis), and its callback ACK satisfies the real outbox drain — so the rig cannot
silently drift off-contract.
