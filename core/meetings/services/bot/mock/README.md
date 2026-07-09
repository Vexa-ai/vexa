# meetings/services/bot/mock — the MOCK BOT (Lane A instrument)

**Concern:** a contract-faithful stand-in for the real bot, so the **backend** can be validated in
isolation (SoC: backend ⊥ worker) at **L3** on the real compose stack — no browser, no STT, no GPU,
runnable anywhere (ARCH §5; P5/P16). It is the instrument the real bot's L4 capture-quality lane
(`meetings/services/bot/eval/`) is deliberately *not*: cheap and broad, reserving the scarce L4 oracle.

**How it stays faithful:** it reuses the bot's REAL `createOrchestrator` + the REAL adapters
(`lifecycle-http` · `transcript-redis` · `acts-redis` + a recording uploader); only the two heavy
ports are faked — `JoinDriver` → `fakeJoinDriver`, `Pipeline` → `fakePipeline` (`scenarios.ts`). The
backend therefore sees prod-identical lifecycle.v1 emission, and the mock **cannot** emit off-contract
— re-proven by `mock.test.ts` (every emission validated vs the sealed `lifecycle.v1`/`transcript.v1`).

**Surface:**
- `scenarios.ts` — the scenario registry + the two fake ports. Scenario via env `MOCK_SCENARIO`.
- `main.ts` — the mock composition root (real adapters + the fakes); the worker entrypoint baked into
  `mock-bot:dev` (swapped in via `BROWSER_IMAGE` for the `MOCK_BOT=1` `gate:compose` run).
- `mock.test.ts` — the L2 fidelity test (offline; `npx tsx mock/mock.test.ts`).

**Scenarios:** `normal · join-timeout · reject · crash · immediate-stop · continue · speak-ack ·
emit-n-segments · slow-join · recording` — one per backend behaviour (lifecycle FSM · join-retry ·
attributable failure · max-bots-adjacent · recording receiver · acts round-trip).

**Depends on:** `../src/{orchestrator,contracts,config,ports,adapters/*}` (the real bot, reused). It is
**not** a published library (no consumers import it) — it is a test/validation worker.
