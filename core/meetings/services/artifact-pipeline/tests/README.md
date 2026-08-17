# artifact-pipeline/tests

```bash
uv run pytest -q        # no docker, no network, no model
```

Two ends are faked and one is never faked. The meeting API is served through
`httpx.MockTransport` (or the shipped `CorpusTransport`) so the real HTTP client is the code
under test; the delivery sink records instead of mailing. **The pre-send gate is always the
real module** — a fake gate would let a change that quietly widens the recipient list pass,
which is the one failure this service exists to prevent.

| File | Covers |
|---|---|
| `test_artifact_schema.py` | the schema, section order, and the header fields the postman parses |
| `test_template_renderer.py` | what the deterministic renderer claims, and what it refuses to claim |
| `test_gateway.py` | route preference, fall-through, empty-vs-absent, the record's own id |
| `test_delivery.py` | the three sinks and the postman CLI contract |
| `test_pipeline.py` | the spine on the real gate: fan-out, holds, suppression, idempotency |
| `test_corpus_pipeline.py` | the whole archive — **skips** without `VEXA_ARTIFACT_CORPUS` |
| `test_postman_contract.py` | the real chat-door parser — **skips** without `VEXA_CHAT_DOOR_SRC` |

Two suites skip by default because what they need is not in this repository:

```bash
VEXA_ARTIFACT_CORPUS=/path/to/corpus uv run pytest tests/test_corpus_pipeline.py -q -s
VEXA_CHAT_DOOR_SRC=/path/to/chat-door/src uv run pytest tests/test_postman_contract.py -q
```

The corpus is 22 recordings from the founder's own archive and two of them are the private
material the gate exists to keep off an email thread; it will not be vendored. The chat door
is a separate service on an unmerged branch, and a test may not import across that boundary
in the normal case.
