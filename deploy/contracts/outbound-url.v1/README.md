# outbound-url.v1 — may a server fetch a URL somebody else supplied

> **SEALED** — `outbound-url.schema.json` is pinned in `contracts.seal.json`; a change rides the
> human `lane:contract` review (`pnpm seal:contracts` re-pins the hash).

Several images fetch URLs a person or a model supplied: customer webhooks and calendar feeds
(meeting-api), the agent's web and asset fetches and the model endpoint (agent-api and the worker),
the transcription endpoint check (admin-api), custom-service calls and OAuth exchanges (the
credential broker), and the bot's transcription client. Each must refuse a URL that reaches this
deployment or its cloud: one rule, written here.

**The rule** (`ssrf.py` states it in full):
1. every address is checked as what it reaches — an IPv6 form that carries an IPv4 address
   (mapped, compatible, translated, 6to4, Teredo, NAT64, ISATAP) and a numeric host the resolver
   reads as IPv4 are read for that IPv4 address;
2. the refused ranges are explicit lists, not `ipaddress` flags;
3. the connection is pinned to the address that was checked, on every request and redirect;

and a host name that can only name this deployment (`localhost`, `*.localhost`, a cloud metadata
name, a single label) is refused before any lookup.

| File | What it is |
|---|---|
| `ssrf.py` | the canonical implementation (Python, stdlib only). Vendored verbatim into each Python image that fetches such a URL; `scripts/parity.json` fact `outbound-url-guard` fails the build when a copy differs. |
| `outbound-url.schema.json` | the shape of the case table (sealed). |
| `golden/outbound-url-vectors.json` | the case table: addresses, host names and URLs, each with its verdict. Both implementations' test suites read a byte-identical copy (fact `outbound-url-vectors`): meeting-api `tests/test_outbound_url_vectors.py` and `@vexa/transcribe-whisper` `src/url-guard.test.ts`. |
| `validate.mjs` | `gate:schema`: the golden and every copy a suite reads conform. |

**Changing the rule:** edit `ssrf.py` and the table here, copy both verbatim over every site the
two parity facts list, and keep both suites green. The seal covers the table's shape; the cases and
the code are held by the parity facts and the suites (ADR-0044).

## How it works

1. **A person saves a URL.** Where a URL is stored before it is used, the store checks it as
   written, with no lookup (`validate_url(..., resolve=False)`): scheme `http`/`https`, a host,
   no literal internal address, no deployment-only name. A refusal is a 422 the person sees.
2. **A server fetches it.** The fetching service resolves the host and checks every address it
   resolves to (`validate_url` returns a `PinnedURL`).
3. **The connection is pinned.** The pinned transports (`build_pinned_transport`,
   `build_pinned_sync_transport`) re-resolve and re-check at connect time and dial the checked
   address with the original `Host` header and TLS SNI, on every request and redirect. The
   TypeScript twin does the same through Node's `lookup` hook (`guardedLookup`).

Where each service applies it:

| Service (copy) | URL checked | Where |
|---|---|---|
| meeting-api (`webhooks/ssrf.py`) | customer webhook, at send; calendar feed fetch | `webhooks/delivery.py`, `__main__.py` (pinned transports), `calendar_sync/adapters.py` |
| admin-api (`app/ssrf.py`) | saved webhook (`PUT /user/webhook`), saved transcription endpoint (`PUT /user/transcription`), saved calendar feed | `app/main.py`, `app/calendars.py` |
| agent control plane (`shared/ssrf.py`) | asset and page-image fetches; model endpoint host; repository host; the Settings transcription-endpoint test | `shared/asset_source.py`, `shared/page_images.py`, `control_plane/model_endpoint.py`, `control_plane/repo_ref.py`, `control_plane/config_test.py` |
| agent worker (`llm/ssrf.py`) | WebFetch | `llm/web_tools.py` |
| credential broker (`ssrf.py`) | custom-service calls and OAuth exchanges: the host must resolve only to public addresses | `secret_service.py`, `service_oauth.py` |
| bot (`@vexa/transcribe-whisper` `url-guard.ts`) | the person's own transcription endpoint, on every request (`publicOnly` when the endpoint's owner is the customer) | `transcription-client.ts` |

The deployment's own endpoints (its bundled transcription service, its own model gateway) are
operator configuration and are not held to this rule.

**The one operator exception: `OperatorAllowance`.** An operator may name internal hosts and
networks that ONE kind of fetch may reach, today calendar feeds served inside a corporate network
(`VEXA_CALENDAR_FEED_ALLOW`, read by meeting-api's ICS fetch and admin-api's save check).
`parse_allowance` builds it from deployment configuration and refuses a wildcard, a URL, a malformed
name, `localhost` and metadata names, and any network overlapping loopback, link-local (cloud
metadata), unspecified, multicast or reserved addresses. The check never admits those addresses even
when an entry seems to cover them. A listed host name may resolve to private addresses; other hosts
are admitted only to public addresses or addresses inside a listed network. The caller passes it
explicitly (`allow=`) to `validate_url`, `revalidate_at_connect` and the pinned transports; every
caller that passes nothing (webhooks, the agent's fetches, the broker) is unchanged, and the pin and
connect-time re-check apply to allowed fetches exactly as to others.

**Ownership.** The rule has one canonical copy, `ssrf.py` here, and one table,
`golden/outbound-url-vectors.json`. Each image carries a verbatim copy; no service writes or
changes the rule at runtime. Paths above are relative to each service's source root.

## Why it complies

| Rule | How this contract meets it | Gate |
|---|---|---|
| P4: a cross-process contract is sealed | The case table's shape is sealed in `contracts.seal.json` | `gate:schema`, `gate:contract-version` |
| P8: the goldens are the spec | Both language suites are held to every row of the one table | `gate:python`, `gate:node` |
| P2 and ADR-0044: images do not import each other | The module is vendored per image, not imported across images | `gate:isolation-py` |
| P23 and ADR-0044: no duplicated fact drifts | Every vendored copy is byte-compared with this one, and every copy of the table with the golden | `gate:fact-parity` (facts `outbound-url-guard`, `outbound-url-vectors`) |
| P18: fail loud | A refusal is a typed `SSRFError` (`SsrfError` in TypeScript) with a message the person sees; the bot's refusal is a non-retryable transcription fault | ungated: reviewed |

**Security.** Any caller may supply a URL; no caller may make a service reach loopback, private,
shared, link-local, metadata, documentation, reserved or multicast addresses, in any IPv4 or IPv6
notation, or a name that resolves to one. A record changed between check and connect is
re-checked at connect.

Deny tests:

| Where | Test file | Tests |
|---|---|---|
| meeting-api | `core/meetings/services/meeting-api/tests/test_webhook_ssrf.py` | `test_blocked_urls`, `test_dns_rebinding_to_private_blocked`, `test_sink_blocks_ssrf_without_touching_transport`, `test_every_form_of_an_internal_address_is_refused`, `test_a_name_resolving_to_any_form_of_an_internal_address_is_refused`, `test_a_connect_time_rebind_to_a_transition_form_never_dials` |
| meeting-api, the case table | `core/meetings/services/meeting-api/tests/test_outbound_url_vectors.py` | `test_address`, `test_hostname`, `test_url` |
| admin-api | `core/identity/services/admin-api/tests/test_saved_url_guard.py` | `test_an_internal_feed_is_refused`, `test_a_saved_url_is_checked_without_a_lookup` |
| meeting-api, the operator allowance | `core/meetings/services/meeting-api/tests/test_calendar_feed_allow.py` | `test_everything_else_stays_refused_with_an_allowance`, `test_a_listed_host_that_rebinds_to_metadata_at_connect_is_never_dialled`, `test_the_allowance_never_reaches_customer_webhooks`, `test_an_unsafe_or_malformed_entry_refuses_the_boot` |
| admin-api, the operator allowance | `core/identity/services/admin-api/tests/test_calendar_enterprise.py` | `test_with_an_allowance_everything_else_internal_is_still_refused`, `test_an_unsafe_allowance_entry_is_refused` |
| agent | `core/agent/tests/test_outbound_url_guard.py` | `test_webfetch_refuses_every_notation`, `test_the_asset_fetch_refuses_every_notation`, `test_the_repository_host_check_refuses_every_notation`, `test_a_name_resolving_into_any_notation_is_refused`, `test_a_record_flipped_after_the_check_is_never_dialled`, `test_webfetch_refuses_a_rebind_at_connect`, `test_the_stt_test_never_probes_an_internal_customer_endpoint` |
| agent, model endpoint | `core/agent/tests/test_model_endpoint.py` | `test_a_non_allowlisted_endpoint_is_refused`, `test_a_wildcard_never_reaches_an_internal_address_in_any_notation`, `test_a_wildcard_never_reaches_the_deployments_own_network` |
| bot | `core/meetings/modules/whisper/src/url-guard.test.ts` | checks `address <addr>`, `url <url>` (every table row), `lookup refuses when any answer is internal`, `customer endpoint refused: …`, `the internal server was never reached` |
