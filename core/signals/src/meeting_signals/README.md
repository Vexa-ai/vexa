# meeting_signals — the signals service's code

| module | role |
|---|---|
| `__main__.py` | composition root: preflight, the runner, `/health` |
| `source.py` | the input port (`discover`, `open`, `read`, `ack`, `context`, `wake_target`, `brief`) |
| `meeting_feed.py` | the meeting adapter: segment hints → verified `tc:meeting:{row}` feed, the brief from agent-api |
| `chat_feed.py` | the room adapter: `room:{session}:msgs`, the fixed rule tier, the agent's words as context |
| `rules.py` | the agent's own rule triggers (keyword, person, guarded regex) |
| `core.py` | the listener: buffer, scoring, caps, wake decision — source-agnostic |
| `scoring.py` | the information measure (formula in the module docstring) |
| `brief.py` | the listening brief, its defaults and its fixed rules |
| `model.py` | the signal-model port: `jev` and `openai-compatible` adapters, strict parsing |
| `keys.py` | the model key from the credential broker (dev fallback documented there) |
| `policy.py` | `_global/POLICIES.md`: on/off, the model, the caps |
| `store.py` | signals' own redis keys (lease, state, seen ids, annotations), fenced writes |
| `wake.py` | the wake handed to flows-api as a `meeting.signal` fact |
| `runner.py` | one asyncio task per live source, leases, takeover |
| `replay.py` | replay a transcript JSON and print the wakes |
| `broker_assertion.py`, `config_preflight.py` | vendored contract code (byte-identical, gated) |
