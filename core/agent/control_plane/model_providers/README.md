# agent · control_plane · model_providers

The operator's model catalog, and the provider port each model resolves through. Design:
[ADR-0043](../../../../docs/adr/0043-model-choice-catalog-behind-a-provider-port.md).

**One concern:** given the catalog an operator declared (`VEXA_MODEL_CATALOG`, `models.v1` Catalog),
answer three questions per person — *which models may I pick*, *which do I run on before I pick*,
and *what route does this one take*. The route is data; `control_plane/dispatch.py` turns it into a
worker's environment (`route_env`) in the one place a worker's route has always been decided
(`apply_model_route`).

## Public surface (the front door, `__init__.py`)

| Name | What |
|---|---|
| `parse(raw, env)` / `load(env)` | the catalog, or `CatalogError` naming every problem — refused whole, at boot, never quoting a value |
| `Catalog` | `.route(pick, ctx, admin=)` (the port, end to end) · `.choose(...)` · `.listing(...)` (`models.v1` ModelList) · `.default_for(...)` · `.empty` |
| `ModelRoute` | harness · endpoint · credential source and value (never in a repr) · key header · model at the provider · extra body · capabilities |
| `ModelProviderPort` | what an adapter implements: `kind`, `fields`, `check`, `harness`, `available`, `route` |
| `RouteContext` | what an adapter may consult: the person's Settings → Models, secret resolution, the operator gates, the deployment's harness and model — injected, never read from the process by an adapter |
| `ModelChoiceFault` | a pick that cannot run, typed (`source: model-provider` + `kind`), naming the model and the provider |

## Modules

- `port.py` — the port, the route, the fault, the context.
- `catalog.py` — parse and validate the declaration; select per person; list per person.
- `adapters/` — one module per provider kind; see its README.

## Rules

- **No secret value ever leaves this package except inside a `ModelRoute`** — not in a refusal, a
  log line, a listing or a repr. A refusal names the field or the reference, never the value.
- **An explicit pick is never silently replaced.** A pick that cannot run is a `ModelChoiceFault`;
  only a stale *default* is skipped (and logged).
- **The adapter table is the authority on kinds**; `models.v1`'s `Adapter` enum is held equal to it
  by `tests/test_model_catalog.py`.

May depend on: `contracts` (the `models.v1` validators), `control_plane.model_endpoint` (the
operator's endpoint gate, for `custom`). Nothing else in agent-api; nothing in `llm/` or `worker/`.
