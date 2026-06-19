# ADR 0009 — The agent domain is Python; it consumes `transcript.v1` as a schema-by-path seam

**Status:** accepted · 2026-06-19 · enforces **P3, P4, P13**

## Context

The `agent` domain is the LLM/tooling + execution runtime: it turns a `transcript.v1` into a governed
action committed to a user workspace (`workspace.v1`), running as a worker spawned via `runtime.v1`. Its
only cross-domain coupling is to `meetings` — and the constitution's **`meetings ⊥ agent`** rule forbids
importing meetings internals: the agent may reference `meetings/contracts/transcript.v1` (the published
seam) and nothing else. We must also pick the domain's language (P13: add a language only when an
ecosystem forces it; align every language boundary with a service + contract boundary).

## Decision

- **The agent domain is Python.** The LLM/tooling ecosystem and the existing `runtime/` kernel are both
  Python; `agent` sits on the kernel and shares its idioms. `agent/services/agent-api` mirrors the kernel's
  structure (`pyproject.toml` + `src/` + `tests/`, pydantic + uv), so **`gate:python` covers it** with no
  new gate. (`meetings` stays TypeScript where the browser forces it; the two domains meet only at a
  contract.)
- **`transcript.v1` is consumed as language-neutral JSON Schema, read by path.** The agent loads
  `meetings/contracts/transcript.v1/transcript.schema.json` from the filesystem and validates inputs with
  `jsonschema`; it **never imports meetings code**. The committed `golden/` vectors are its test fixtures (P8).
- **The agent re-states the transcript shapes it needs as its own internal models.** A small, deliberate
  duplication — the price of a hard boundary.

## Consequences

- **`meetings ⊥ agent` becomes mechanically unbreakable**, enforced by the language + format seam, not by
  convention: a Python process *cannot* `import` a TS module, and a test asserts `import meetings` raises
  `ModuleNotFoundError`. This is the strongest possible form of P4's "couple only through contracts."
- The agent domain stays self-contained and liftable; its deps (pydantic / jsonschema / …) are a separate
  tree from the npm side. **Python licence scanning (`pip-licenses`, ADR-0004) is owed once these deps grow**
  — today's set is all Cat-A permissive (MIT/BSD), but the gate is npm-only for now.
- Real transports (git `WorkspacePort`, HTTP `RuntimePort`→runtime.v1, redis/bus `TranscriptSource`) are
  ports with deferred adapters (P16) — wired at the service composition root in a later increment.
