# golden vectors — models.v1

Committed example vectors. Each `<Shape>.<case>.json` validates against `#/$defs/<Shape>` via `../validate.mjs`. The goldens ARE the spec (P8).

- `Catalog.self-hosted-and-openrouter.json` — the operator's declaration the docs and deploy values carry: a self-hosted Qwen 3 on an OpenAI-compatible endpoint, OpenRouter (admins only), Anthropic on the deployment's subscription, and the person's own endpoint. `core/agent/tests/test_model_catalog.py` parses it.
- `ModelList.member.json` — what `GET /api/models/catalog` serves that catalog to a member with no own endpoint: the admins-only and own-endpoint entries are absent, and nothing names an endpoint or a credential.
- `ModelList.chat.json` — the same, asked for one chat (`?session=`), whose own pick is `claude`.
- `ModelList.empty.json` — a deployment with no catalog.
