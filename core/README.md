# core — the platform backend (runtime · meetings · agent · identity · gateway)

The five backend domains live here so the tree reads itself: **`core/` is the runnable platform**;
everything else in the repo (`clients/`, `sdks/`, `integrations/`, `deploy/`, `tools/`) consumes it
across a published contract seam.

| Domain | Role |
|---|---|
| [`runtime/`](runtime/) | ① **kernel** — spawn/execute workloads (process · docker · k8s) · mounts the workspace · domain-agnostic |
| [`meetings/`](meetings/) | ② **capture** — meeting-api · bot · transcription · tts · `eval/` → transcript + events |
| [`agent/`](agent/) | ③ **execution** — agent-api · sandboxed worker (scoped identity + a mounted workspace) |
| [`identity/`](identity/) | access · accounts · tokens · audit — authN/authZ |
| [`gateway/`](gateway/) | the edge — auth · routing · WS fan-out |

## `core/` is an ORGANIZING folder, not a domain

It owns **no code and no contract of its own** — it only groups. So the bounded-context rules
(P1–P3) and the dependency seam apply **per domain, one level down**, exactly as before the grouping:
each domain's published contracts nest in `core/<domain>/contracts/`, a domain's internals
(`services/`, `modules/`) may import only its own code, another domain's `contracts/`, and
`core/runtime/contracts` — never another domain's internals (`gate:graph`).

The move into `core/` is purely structural: the sealed contracts are byte-identical (only their
registry keys re-prefixed to `core/…`), and the full gate suite stays green.

_The per-domain bounded-context rules (P1–P3) and the dependency seam are summarized above; see the
[architecture docs](../docs/docs/architecture/README.md) for modules, dispatch, execution, and governance._
