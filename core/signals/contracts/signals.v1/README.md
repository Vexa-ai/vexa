# signals.v1

The transcript-signals wire. Owned by the `signals` domain.

| Shape | Where it travels | Writer | Readers |
|---|---|---|---|
| `Brief`, `ListenResponse` | `GET /internal/meetings/{row}/listen?subject=<owner>` on agent-api (internal secret); carries the policy too | agent-api (the `meeting_listen` tool writes the brief) | signals |
| `Annotation` | redis stream `sig:{kind}:{id}` (`sig:meeting:{row}`, `sig:chat:{session}`), one `payload` field holding the JSON | **signals only** | gateway → terminal |
| `WakeData` | the `data` of the event.v1 `meeting.signal` POSTed to flows-api `/events/signal` (`X-Internal-Secret`, `X-Vexa-Service: signals`) | signals | the `_global` flow that reacts to it |
| `ModelSignals`, `ModelSignalsTiers` | the `model_signals` key of `_global/POLICIES.md` (one model, or the allowed models by tier) | the company admin | signals |
| `Rule`, `Brief.model_tier` | the agent's own rule triggers and model tier in the brief ("the agent owns the sensor") | agent-api (`meeting_listen`) | signals |

The transcript text in `BufferLine` is meeting speech: anyone in the call can say anything, so every
consumer treats it as data and never as instructions. A credential never appears here: `secret_ref`
names a secret the credential broker keeps (`broker:signals/<name>`), or `none` for a keyless endpoint.

Run `node validate.mjs --check` (gate:schema).
