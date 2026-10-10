# ADR 0043 — Model choice: an operator-declared catalog behind a provider port, picked per chat

**Status:** proposed · 2026-10-10 · for v0.13.2 ([#1796](https://github.com/Vexa-ai/vexa/issues/1796)) ·
applies P4, P5, P14, P18, P20 and P23 · keeps the route invariants of
[#1783](https://github.com/Vexa-ai/vexa/issues/1783)

## Context

A worker's model was decided by two dials:

- the deployment's model and harness (`VEXA_AGENT_MODEL`, `VEXA_RUNNER`, `VEXA_LLM_*`);
- each person's Settings → Models: a free-form model name, an optional own endpoint, and a harness
  name.

Switching models meant leaving the chat and editing a setting (#1040). A deployment could not offer
a self-hosted model and a hosted gateway side by side: every second model was some person's
hand-typed endpoint.

The route itself was already decided in one place: `dispatch.overlay_model_config` and
`subject_route_env`. That decision carries three invariants, and model choice must keep all three:

1. a person's credential goes only to that person's endpoint;
2. the deployment's credential never goes to a person's endpoint;
3. one place decides the route.

## Decision

1. **A provider port.** `ModelProviderPort.route` (`control_plane/model_providers/port.py`) turns
   one catalog entry into a `ModelRoute`. The route carries:
   - the harness;
   - the endpoint;
   - the credential source (`none`, `secret`, `subscription` or `subject`), and the credential's
     value when it is a secret agent-api holds (never in a repr);
   - the header the key goes in;
   - the model's id at the provider;
   - the extra request fields;
   - the capabilities (tool calling, streaming, context size).

   The port decides nothing else and writes nothing.

2. **One adapter per provider kind** (`model_providers/adapters/`). An adapter is data plus a small
   `route`.

   | Adapter | Harness | Endpoint | Credential |
   |---|---|---|---|
   | `openai_compatible` | openai-agent | declared (vLLM, llama.cpp, LiteLLM, a gateway) | none or secret |
   | `openrouter` | openai-agent on `/api/v1`, or claude-code on `/api`, as declared | fixed | secret, as a bearer token |
   | `anthropic` | claude-code | Anthropic | secret as `x-api-key`, or the deployment's subscription |
   | `custom` | the person's own | the person's own Settings → Models endpoint, still gated by `VEXA_MODEL_BASE_URL_ALLOW` | the person's own key |

   Adding a kind means adding an adapter module and its `models.v1` enum entry. A test holds the
   enum and the adapter table equal. The dispatch never changes.

3. **An operator-declared catalog.** `VEXA_MODEL_CATALOG` is one JSON object validated against
   `models.v1#/$defs/Catalog` (P14). It declares:
   - **providers**, each naming an adapter and that adapter's settings;
   - **models**, each with a catalog id, a display name, the id at the provider, capabilities,
     `access` (`everyone` or `admins`) and at most one `default`.

   A credential is only ever `secret_ref: env:NAME`: a variable in agent-api's environment, filled
   from the deployment's secret store (Helm `secretKeyRef`, compose `.env`).

   agent-api refuses a catalog **whole, at boot**, and names every problem without quoting a value:
   - an unknown adapter;
   - an inline secret, whether written as a field, as a value or inside a URL;
   - a duplicate id;
   - two defaults;
   - a model on an undeclared provider;
   - a model without tool calling;
   - a field its adapter does not take;
   - an unresolvable `secret_ref`.

   With no catalog declared, nothing in this ADR runs. The dispatch is byte-identical to before, and
   a test pins that.

4. **The catalog is env-only. admin-api holds only the defaults.** A runtime override that could
   name endpoints would be a free-form endpoint set from the UI. The platform `models` setting gains
   `default_model` (the organisation's default), and so does each person's Settings → Models (their
   own default). The resolution is unchanged: the person's value, then the platform's, then the
   catalog's `default`, then the first model the person may use.

5. **`models.v1`, sealed** (P4). It holds the two shapes that cross a boundary:
   - `Catalog`: the operator's declaration;
   - `ModelList`: one person's view, served by `GET /api/models/catalog` to the terminal and to the
     MCP tool `models_list`. It holds only the models that person may pick, their default and, for
     one chat, that chat's pick. It carries no endpoint, no credential and no request setting.

6. **Selection, stored per chat, resolved in one place.**
   - `POST /api/chat/model` validates the pick against the catalog and the caller's role (admin-api's
     `is_admin`, asked only when an entry is restricted), then stores it with the session index's one
     writer, `set_model`.
   - The pick raises the chat's stale-mounts flag, so the next turn gets a fresh worker. A warm
     worker keeps the route it started with.
   - The chat passes the pick to `Dispatcher.dispatch(model=…)`. `dispatch.apply_model_route`, the
     same place that decided the route before, resolves it through the port. `route_env` then stamps
     every key either harness reads, the empty string included.
   - A secret goes to its own endpoint under the one name that endpoint expects. Every other
     credential name is stamped empty. The deployment's subscription token survives only on the
     subscription route, whose endpoint is stamped empty, so the claude CLI sends that token to
     Anthropic and nowhere else.
   - Delegated jobs run in the chat's worker and inherit its route. Routines and events run on the
     person's default catalog id. No run takes a free-form model name.

7. **Failures are typed and attributed** (P18), in the model provider's one fault shape.
   - **A provider call that fails** is `llm.faults.ProviderFault`. The harness classifies it in the
     worker (`unpaid`, `unauthorized`, `rate_limited`, `unavailable`, `refused`), naming the host and
     the model at the provider. A catalog route changes nothing here: the route's endpoint and model
     are what the harness reads.
   - **A pick that cannot run** fails earlier, in agent-api, before any call: `ModelChoiceFault`,
     raised before anything is spawned. Its kinds are the ones only agent-api can decide:
     `unknown_model`, `not_permitted`, `not_configured`, `credential_missing` and
     `endpoint_refused`.
   - **One shape, two producers.** agent-api's image carries no `llm/`, so it produces the same
     record: `source: model-provider` and the same keys (`kind`, `provider`, `model`, `status` as
     `null`, `detail`, `remedy`). A test holds the two key sets and the source equal, and no kind is
     shared. The chat answers a refused pick as `{detail, fault}`, the shape the chat proxy already
     turns into the stream's `error` event, and the terminal renders both producers through
     `surfaces/faults.ts`.
   - **No silent replacement.** An explicit pick is never silently replaced by another model. A
     stale default is skipped and logged, because a stored preference must never stop a turn.

8. **Effort is per chat, and each adapter maps it or refuses it.** An entry lists the effort
   levels it offers (`capabilities.reasoning_efforts`, `default_effort`) in one vocabulary
   (`none` … `max`). The port gains `efforts(provider, model)`, the levels the adapter can send,
   and `route(..., effort)` writes the level into the provider's own field: OpenRouter's
   `reasoning.effort`, the OpenAI `reasoning_effort` or a Qwen `chat_template_kwargs.enable_thinking`
   (chosen per entry by `effort_control`), or the claude CLI's `--effort` (`VEXA_AGENT_EFFORT`).
   A level the adapter cannot send is refused at boot; a pick the model does not offer is the typed
   fault `effort_unsupported`. The pick is stored with the chat's model and cleared when the model
   changes. On a catalog route the effort comes only from the catalog, so a Settings → Models effort
   never reaches a model that did not offer it. An entry may also set `max_output_tokens`, which
   the route stamps as `VEXA_AGENT_MAX_OUTPUT_TOKENS`.

9. **The Test button probes the entry through the same port.** `GET /api/models/test?model=<id>`
   resolves the route the dispatch would stamp, then probes it in its own dialect with exactly its
   own credential, at the URL the harness posts to.

## Consequences

- People switch models from the chat. Operators offer self-hosted and hosted models side by side,
  and each person sees only what they may use.
- **With a catalog declared, the free-form Settings → Models `model` and `runner` no longer pick a
  turn's model.** They still configure the `custom` entry, if the operator declares one.
  `VEXA_MODEL_ALLOWLIST` still gates only a person's own model name.
- **A deployment with a catalog does not need a deployment credential.** The pre-turn credential
  check is skipped when a catalog exists, because every provider's secret resolved at boot.
- **A route is fixed for a worker's life.** A changed catalog or default reaches a warm chat at its
  next cold start; a pick reaches it at the next turn.
- **Two follow-ups are left for 0.13.3:**
  - a per-routine model: a `model` field on `routine.v1` and `unit.v1` needs a re-seal;
  - provider-required HTTP headers (#1667).

## Alternatives considered

- **A catalog in admin-api, edited in the UI.** Rejected: it is a free-form endpoint and credential
  set from the browser, which the endpoint gate exists to refuse.
- **One adapter per model.** Rejected: provider settings would repeat on every model of a provider,
  and a credential would be declared many times.
- **Silently falling back to the default when a pick cannot run.** Rejected: P18. A turn that runs on
  a model the person did not pick looks like it worked.
