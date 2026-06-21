"""Gateway lane (Group-6) behavioral conformance harness.

Public surface:
- ``contracts`` — load the sealed api.v1 / ws.v1 schemas BY PATH and validate any
  payload against a named component (`#/components/schemas/<Shape>` for api.v1,
  `#/$defs/<Shape>` for ws.v1).
- ``fake_meeting_api`` — the downstream the gateway proxies to, SPLIT: ``/transcripts`` +
  ``/meetings`` are served by the REAL, SHIPPED collector (``transcription_collector.create_app``,
  seeded to the api.v1 goldens) so those conformance assertions drive shipped collector code;
  ``/bots*`` stay a golden port-fake (meeting-api's ``/bots`` serving is not carved into v0.12 yet).
- ``gateway_app`` — `build_gateway()`: constructs the PRODUCTION `gateway.create_app`
  (the shipped app) injected with the split downstream + fake admin-api authorizer, so the
  REST conformance assertions drive shipped code.
- ``ws_harness`` — fakes (FakeWebSocket / FakeRedis) + a `CollectorAuthorizer` whose
  `/ws/authorize-subscribe` hop POSTs the REAL collector; `WSMultiplexHarness.run()` drives the
  production `gateway.app._run_multiplex` (subscribe → subscribed ack; forwarded redis payload →
  data frame; malformed → Error).
- ``obs`` — re-exports the production trace emitter (`gateway.obs`) so the tracing eval
  installs its sink on the SAME emitter the shipped app uses.

Import direction: conformance (test) → gateway (prod) + transcription-collector (prod). Neither
production package imports anything from conformance.
"""
