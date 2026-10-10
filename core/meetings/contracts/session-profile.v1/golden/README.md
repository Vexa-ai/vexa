# golden — session-profile.v1 vectors

Checked by [`../validate.mjs`](../validate.mjs) against [`../session-profile.schema.json`](../session-profile.schema.json):

- `WritebackBody.<case>.json` — accepted write-back bodies: they conform to the shape and pass the
  profile's rules. `max-files` is the count limit itself (128 entries).
- `WritebackResult.<case>.json` — the route's 200 answer.
- `Refused.<case>.json` — `{why, body}`: the body must be refused, for the reason `why` gives
  (`too-many-files` is one entry past the limit).
- `PathVectors.<case>.json` — paths every matcher must answer `inProfile`. The same files drive the
  TypeScript matcher (`@vexa/remote-browser` `src/session-store.test.ts`) and the Python one
  (meeting-api `tests/test_session_profile.py`), which also parses every `WritebackBody` and refuses
  every `Refused` body.

The size limits (8 MiB a file, 32 MiB together) are not goldens, because a vector at the limit would
be an 11 MB file; meeting-api's tests drive them by lowering the limit.
