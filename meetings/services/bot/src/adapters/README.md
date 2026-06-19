# adapters — the bot's live transports (P5)

The real implementations of the bot's ports, injected at the composition root (`../index.ts`). The pure
orchestrator never imports these — it speaks only the port interfaces (`../ports.ts`), so the control flow
stays offline-provable; these adapters bind those ports to redis / HTTP.

**Surface (increment 2a — live):**
- `lifecycle-http.ts` — `createHttpLifecycleSink` → `LifecycleSink`: POST `lifecycle.v1` to `meetingApiCallbackUrl` (`x-internal-secret` header, bounded retry/backoff, never throws out of `emit`). Native `fetch`, no dep.
- `transcript-redis.ts` — `createRedisTranscriptSink` + `redisClientFrom` → `TranscriptSink`: `XADD transcription_segments` + `PUBLISH tc:meeting:{id}:mutable` (`transcript.v1`).
- `acts-redis.ts` — `createRedisActsSource` + `redisActsClientFrom` → `ActsSource`: `SUBSCRIBE bot_commands:meeting:{id}` → `parseAct` → handler (`acts.v1`).

**Deps:** `redis` (node-redis v4) for the redis factories; native `fetch` for HTTP. **May depend on:** the bot's own `../ports.ts`/`../contracts.ts` + the redis client. **Tests:** `*.test.ts` here are L3 — offline, with injected fake clients/fetch (no real redis, no network); the real node-redis bindings are type-checked, validated live against a real broker at the P3 integration barrier.

**Pending 2b:** the browser-coupled adapters (join via `@vexa/join`+`@vexa/remote-browser`, the capture→pipeline, recording upload) are still stubbed in `../index.ts`.
