# ADR 0001 — Contract spec format & language minimalism

**Status:** accepted · 2026-06-18

## Context
Contracts cross a TS↔Python seam (TS for browser capture/bot/clients; Python for ML inference + the
incumbent control plane). We need a language-neutral way to define them — without over-tooling six
hand-written schemas.

## Decision
- **JSON Schema (Draft 2020-12) is the single source of truth** for every published contract, with
  golden vectors as the spec (P8) and ajv validating goldens ≡ schema (`gate:schema`).
- **A contract describes data, not transport auth.** Tokens/credentials are a transport-layer concern
  (authenticate the producer/connection), **never a field in a data contract** (e.g. no `token` in
  `transcript.v1`).
- **No codegen pipeline, no schema lint, no IDL (TypeSpec) now.** Consumers validate against the
  schema *at runtime* (ajv in TS, pydantic/jsonschema in Python — zero codegen). Typed bindings are
  hand-written or generated per-consumer only when a real consumer needs them (Stage 3/4).
- **Language minimalism (P13):** TS where the browser forces it; Python for ML + the control plane;
  no third language without a forcing function — each language multiplies the schema surface.
- **Codegen targets follow the seam:** `runtime.v1` is Py↔Py → Python-only for now; the TS↔Py
  contracts get both bindings when their consumers exist.

## Consequences
- The contract tier stays minimal: `*.schema.json` + `golden/` + `generate.mjs` (ajv).
- Revisit TypeSpec only if authoring verbosity becomes a real cost (many schemas).
- P4's "both-language conformance" is realized per-consumer in Stage 3/4, not pre-built.
