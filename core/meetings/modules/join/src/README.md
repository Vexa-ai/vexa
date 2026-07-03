# join/src — the join layer's code

The public surface is [`index.ts`](index.ts) (P6); everything else is internal and
imports host symbols only from [`_host.ts`](_host.ts) (the one seam back to the embedder).

| Path | Concern |
|---|---|
| `index.ts` | front door: `joinMeeting`, `resolvePlatform`, re-exports |
| `_host.ts` | the contract/port: `BotConfig`, `Hooks`, `JoinState`, state callbacks (Node builtins only) |
| `browser-args.ts` | canonical Chromium launch flags (`JOIN_BROWSER_ARGS`) |
| `googlemeet/` · `msteams/` · `zoom/` | per-platform join · admission · leave · removal · selectors |
| `shared/` | cross-platform helpers (the debug/escalation view) |

Depends on `playwright` + Node builtins only (gate:isolation).
