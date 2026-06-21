# golden — invoke.v1 vectors

Conforming `Invocation` examples, one per trigger, validated against `../invoke.schema.json` by
`../validate.mjs` (filename `<Shape>.<case>.json` → `#/$defs/<Shape>`):

- `Invocation.meeting-completed.json` — a post-session run.
- `Invocation.chat-invocation.json` — a user ask mid/after the meeting (carries a `prompt`).
- `Invocation.scheduled.json` — a `runtime.v1` schedule fired (minimal: no platform/prompt).
