# agent · control_plane · model_providers · adapters

One module per provider **kind** — the `adapter` value a catalog provider names. `__init__.py`'s
`ADAPTERS` is the one table of kinds.

| Kind | Module | Harness | Endpoint | Credential |
|---|---|---|---|---|
| `openai_compatible` | `openai_compatible.py` | openai-agent | declared `base_url` (vLLM, llama.cpp, LiteLLM, a gateway) | `none`, or a `secret_ref` sent as a bearer token |
| `openrouter` | `openrouter.py` | openai-agent (`/api/v1`) or claude-code (`/api`), as declared | fixed | a `secret_ref`, sent as a bearer token; every claude-code tier pinned to the chosen model |
| `anthropic` | `anthropic.py` | claude-code | Anthropic | a `secret_ref` sent as `x-api-key`, or the deployment's own subscription |
| `custom` | `custom.py` | the person's own | the person's own Settings → Models endpoint, gated by `VEXA_MODEL_BASE_URL_ALLOW` | the person's own key |

`common.py` holds the rules every adapter applies the same way: an unexpected declaration field is
refused (never ignored), a model's capabilities and merged extra body, and a `secret_ref` resolved —
a missing one is a typed `credential_missing` refusal naming the reference.

**Adding a kind:** a module here implementing `ModelProviderPort`, one line in `ADAPTERS`, its name
in `models.v1`'s `Adapter` enum (a re-seal), and its cases in `tests/test_model_provider_port.py`.
`control_plane/dispatch.py` does not change.
