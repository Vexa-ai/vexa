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
| `Catalog` | `.route(pick, ctx, admin=, effort=)` (the port, end to end) · `.choose(...)` · `.choose_with_effort(...)` · `.effort(...)` · `.listing(...)` (`models.v1` ModelList) · `.default_for(...)` · `.empty` |
| `ModelRoute` | harness · endpoint · credential source and value (never in a repr) · key header · model at the provider · extra body (with the effort written in its provider's field, on openai-agent) · capabilities · effort (claude-code `--effort`) · max output tokens |
| `ModelProviderPort` | what an adapter implements: `kind`, `fields`, `check`, `harness`, `available`, `efforts`, `route` |
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

## How it works

1. **The catalog.** The operator declares `VEXA_MODEL_CATALOG` (one JSON object, `models.v1`
   Catalog) on agent-api, from compose `.env`, Helm `models.catalog` or Lite `.env`. Each
   credential is a `secret_ref: env:VEXA_MODEL_SECRET_<NAME>` delivered beside it (Helm
   `models.catalogSecrets`, each from a Secret the operator names). agent-api parses it once at boot
   (`catalog.load`) and refuses it whole, naming every problem, if anything is wrong — schema,
   adapter rules, effort levels an adapter cannot send, inline secrets, references outside the prefix.
2. **The listing.** `GET /api/models/catalog[?session=]` (agent-api, `routers/admin.py`) returns
   `Catalog.listing` for the caller: only the models their role and Settings allow, their default,
   and for a named chat its model and effort pick. The MCP tool `models_list` re-enters the same
   route (`routes.v1`, `mcp_reentry`). No endpoint, credential or request setting is in it.
3. **The pick.** The terminal's picker posts `POST /api/chat/model {session, model, effort}`
   (`routers/chats.py`). agent-api checks it with `Catalog.choose_with_effort` against the same
   person's context; a pick the next turn could not run is a `ModelChoiceFault` (`{detail, fault}`)
   and is not stored. A stored pick is written by `_Sessions.set_model` — the only writer of a
   chat's `model` and `effort` on the session index — which also marks the chat for a fresh worker.
   A person's default model is Settings → Models `default_model`, owned by admin-api.
4. **The dispatch.** On the chat's next turn, `routers/chats.py` reads the pick and calls
   `Dispatcher.dispatch(model=, effort=)`. `dispatch.apply_model_route` — the one place a worker's
   route is decided — resolves it through `Catalog.route`: the chosen entry's adapter returns a
   `ModelRoute` with the effort mapped onto its provider's field. A pick that can no longer run
   refuses the turn before anything is spawned.
5. **The worker env.** `dispatch.route_env` stamps the whole route, every key every time, empty
   included: endpoint, the one credential under the header its provider expects, the model, the
   harness, the extra body, `VEXA_AGENT_EFFORT`, `VEXA_AGENT_MAX_OUTPUT_TOKENS` when the entry sets
   one, and `VEXA_AGENT_CONTEXT_TOKENS` as the entry's `context_tokens` less room for the answer
   (`dispatch.context_budget`). A key the route cannot fill is left out, never stamped empty, so the
   deployment's forwarded value applies (`test_an_entry_with_no_window_never_erases_the_deployments_budget`,
   `test_the_route_never_stamps_an_empty_context_budget`). The runtime forwards it (`WORKER_FORWARD_ENV`; a stamped key wins over the deployment's),
   and the harness in the worker reads it. With no catalog, step 4 is the unchanged Settings →
   Models overlay.

**Contracts and routes:** `models.v1` (Catalog, ModelList, ReasoningEffort; sealed) · `unit.v1`
`Fault` with the `model-provider` kinds (sealed) · `config.v1` (`VEXA_MODEL_CATALOG`) · `routes.v1`
(`GET /agent/models/catalog`) · `mcp.tools.v1` (`models_list`). **Configuration lives in** agent-api's
environment only; the catalog never reaches a worker or a bot.

## Why it complies

**Architecture** (rules from `docs/docs/governance/architecture.mdx`):

| Rule | How this design meets it | Enforced by |
|---|---|---|
| P2 couple through contracts | the browser and MCP see only `models.v1` ModelList; refusals travel as `unit.v1` `Fault` | `gate:isolation` |
| P4 published schemas | the catalog, the listing and the fault kinds are sealed `.v1` contracts with goldens | `gate:schema` · `gate:contract-version` |
| P5 adapt at the boundary | each provider's vocabulary (OpenRouter `reasoning.effort`, OpenAI `reasoning_effort`, a Qwen `enable_thinking`, the claude CLI's `--effort`, auth headers) is translated inside its adapter; dispatch never names a provider | review |
| P6 one front door | consumers import `control_plane.model_providers`, never a module inside | `gate:exports` |
| P7 config by env | a worker's route arrives only as stamped env | review |
| P14 config is a validated contract | one JSON env var validated against `models.v1` at boot; credentials only by reference; the `VEXA_MODEL_SECRET_*` variables are operator-named and sit outside `config.v1`'s declared keys on purpose — the prefix is their contract | `gate:config-contract` (for `VEXA_MODEL_CATALOG`); the prefix: ungated, reviewed, and tested below |
| P18 fail loud | a pick that cannot run is a typed `model-provider` fault (`unknown_model`, `not_permitted`, `not_configured`, `credential_missing`, `endpoint_refused`, `effort_unsupported`), never a turn on another model | `gate:schema` (kinds) · tests below |
| P20 default-deny | the listing, the pick and the dispatch each check the caller's role and context; `admins` entries are refused to members at every hop | deny tests below |
| P23 one writer | a chat's pick: `_Sessions.set_model`; a person's default: admin-api; the catalog: the operator's env | `gate:dataflow` |

**Security.**

- **Who sees what.** A person sees the models their role allows, by display name, provider key,
  harness and capabilities. No endpoint, credential, extra body or `secret_ref` reaches the browser
  or an MCP client (`test_no_listing_ever_carries_an_endpoint_or_a_credential`,
  `test_the_listing_never_carries_an_endpoint_or_a_key`, `test_a_member_sees_only_what_they_may_pick`).
- **Authorization at each hop.** The listing, the pick and the dispatch each resolve the caller's
  context and role again; nothing the browser sends is trusted as a route
  (`test_an_admins_only_model_is_refused_to_a_member_and_runs_for_an_admin`,
  `test_a_pick_the_next_turn_could_not_run_is_refused_and_not_stored`,
  `test_a_pick_that_can_no_longer_run_refuses_the_turn_with_a_typed_fault`,
  `test_a_chat_session_id_is_bounded_where_it_enters`).
- **Each credential goes to its own provider only.** The route stamps one credential under the
  header its provider expects and every other credential name empty; the deployment's subscription
  is never sent to a gateway; a person's own endpoint carries only that person's key
  (`test_a_self_hosted_qwen_entry_runs_keyless_on_its_own_endpoint`,
  `test_an_openrouter_entry_sends_the_operators_key_to_openrouter_only`,
  `test_a_keyless_own_endpoint_runs_on_openai_agent_and_never_on_claude_code`,
  `test_on_the_persons_route_the_cli_starts_with_no_stored_credential`,
  `test_a_stored_credential_that_cannot_be_removed_refuses_the_turn`).
- **A catalog reaches only its own secrets.** `secret_ref` must name `VEXA_MODEL_SECRET_*`; the
  resolver reads nothing else; Helm delivers only that prefix from a Secret the operator names
  (`test_a_secret_ref_outside_the_catalog_prefix_is_refused_even_when_it_is_set`,
  `test_the_resolver_never_reads_a_variable_outside_the_prefix`,
  `test_an_inline_credential_is_refused_and_never_quoted`,
  `test_a_missing_secret_is_a_typed_refusal_naming_the_reference_not_the_value`,
  `test_a_route_never_shows_its_credential_in_a_repr`, and `deploy/helm/tests/test_template.sh`:
  "a catalog secret outside VEXA_MODEL_SECRET_* is refused", "a catalog secret needs its own named
  Secret").
- **No free-form endpoint.** People pick catalog ids; a person's own endpoint still passes the
  operator's `VEXA_MODEL_BASE_URL_ALLOW` gate and the outbound URL guard
  (`test_custom_refuses_an_endpoint_the_operator_gate_refuses`).
- **The Test button** probes the chosen entry through the same port. Anyone but an instance admin
  gets the verdict and a typed fault, never the operator endpoint's response body or address
  (`test_a_member_never_sees_the_operator_endpoints_body_or_address`,
  `test_the_test_route_hides_the_body_from_a_member_and_shows_it_to_an_admin`).
- **Effort is never silently dropped or carried over.** A level a model does not offer is refused
  at pick time and at dispatch; a Settings effort never rides into a catalog route
  (`test_an_effort_the_model_cannot_take_is_refused_at_pick_time_and_not_stored`,
  `test_an_effort_the_model_does_not_offer_refuses_the_turn_typed`,
  `test_no_effort_rides_into_a_route_whose_model_did_not_offer_it`,
  `test_a_level_the_adapter_cannot_send_is_refused_at_boot`).
- **Every image ships what agent-api loads.** `deploy/lite/tests/test_contract_schemas.py` and
  `deploy/lite/tests/model-catalog-boot.sh` (catalog unset and set, run in `lite-smoke`).

May depend on: `contracts` (the `models.v1` validators), `control_plane.model_endpoint` (the
operator's endpoint gate, for `custom`). Nothing else in agent-api; nothing in `llm/` or `worker/`.
