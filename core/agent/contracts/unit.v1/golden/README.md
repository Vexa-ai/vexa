# golden vectors — unit.v1

Committed example vectors. Each `<Shape>.<case>.json` validates against `#/$defs/<Shape>` via `../validate.mjs`. The goldens ARE the spec (P8).

`InputVector.*` is a unit-input vector with a fixture secret: `validate.mjs` re-derives the unit key and
the signature in Node, and `core/agent/tests/test_unit_input.py` does it with `shared/unit_input.py`.
`InputEntry.signed.json` is the entry that vector produces.

`Fault.<source>.<kind>.*`, `DoneFrame.*`, `ErrorFrame.*` and `DispatchRefusal.*` are the typed-fault
wire: one `Fault` golden per kind of every source. The Python ones were written by the real emitters
(`shared/runtime_fault`, `llm/faults.classify`, `llm/codex`, `llm/claude_code`, `worker/tool_access`,
`control_plane/unit_faults`); the `agent-api` and `gateway` ones carry the terminal proxy's own text
(`clients/terminal/src/app/api/chat/route.ts`). Hosts, models, times and session ids are synthetic.
