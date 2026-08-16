# chat_door — module map

Read in this order; each module has one job and the docstring at its head states it.

| Module | Job |
|---|---|
| [`config.py`](config.py) | env → `DoorConfig`. Holds the signing key in a wrapper that renders a **fingerprint**, never the material. |
| [`tokens.py`](tokens.py) | mint/verify both token kinds off one HMAC key. Links are single-use; sessions are not. Every rejection has a stable reason. |
| [`scope.py`](scope.py) | the two access questions — which meeting, and may this session see group context. Default-deny; unknown scopes degrade to `guest`. |
| [`store.py`](store.py) | lazy identity: the user row + the personal-instructions markdown doc, created by the first verified click and appended by every steer. |
| [`meetings_client.py`](meetings_client.py) | the door as an HTTP **consumer** of the meeting API. Record-keyed route first, `by-id` fallback. Distinguishes "empty transcript" from "no answer". |
| [`local_records.py`](local_records.py) | dev-only: records off disk when no meetings backend exists. Every page it serves is labelled as such. |
| [`pages.py`](pages.py) | three self-contained HTML pages. Each carries the dev-v0 banner. |
| [`app.py`](app.py) | `create_app()` — the four routes, wiring the above. Store and record source are injectable. |
| [`artifact.py`](artifact.py) | reads a rendered artifact: record id, recipient, meeting label, language; rewrites the placeholder record link. |
| [`postman.py`](postman.py) | artifact → magic link → `multipart/alternative` → SMTP. `--dry-run` writes the `.eml`. |

**The invariant that binds the two halves:** the postman and the door must hold the same
signing key. Nothing else connects them — no shared database, no callback. `/health` publishes
the key's fingerprint so a mismatch is diagnosable without anyone printing a secret.
