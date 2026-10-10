# models.v1 — the model catalog — SEALED

The models a deployment offers, in the two shapes that cross a boundary (P4, P14):

| Shape | Who writes it | Who reads it |
|---|---|---|
| `Catalog` | the operator — `VEXA_MODEL_CATALOG` in agent-api's environment (compose `.env`, Helm `models.catalog`) | agent-api, at boot (`control_plane/model_providers`) |
| `ModelList` | agent-api — `GET /api/models/catalog` | the terminal's model picker, the MCP's `models_list` tool |

A `Catalog` declares **providers** (one per endpoint the deployment reaches, each naming the
**adapter** that resolves it) and **models** on them (a catalog `id`, a display name, the model's id
at the provider, capabilities — including the effort levels a chat may pick (`reasoning_efforts`,
`default_effort`) — an optional `max_output_tokens`, who may pick it, and at most one default). A credential is only ever
a reference — `secret_ref: env:NAME` — never a value; agent-api refuses a catalog with anything
credential-shaped written inline, and names the field rather than the value when it does.

A `ModelList` is one person's view: only the models they may pick (an `admins` model is absent for
everyone else, a `custom` model is absent until they have set their own endpoint), their `default`,
and — when asked for one chat — that chat's own pick (`selected`) and effort pick
(`selected_effort`). It carries no endpoint, no
credential and no request setting: those never leave agent-api.

The adapter kinds (`openai_compatible`, `openrouter`, `anthropic`, `custom`) are held equal to the
adapter table in `core/agent/control_plane/model_providers/adapters/` by
`core/agent/tests/test_model_catalog.py`. Adding a kind is a new adapter module and a new enum entry,
which is a `models.v2` or a human re-seal (`gate:contract-version`).

`gate:schema` validates `golden/` against the schema (`validate.mjs`); the Python side validates
through `contracts.validate_model_catalog_errors` / `validate_model_list`. Design:
[ADR-0043](../../../../docs/adr/0043-model-choice-catalog-behind-a-provider-port.md).
