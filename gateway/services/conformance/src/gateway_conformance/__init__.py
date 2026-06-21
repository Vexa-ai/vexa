"""Gateway lane (Group-6) behavioral conformance harness.

Public surface:
- ``contracts`` — load the sealed api.v1 / ws.v1 schemas BY PATH and validate any
  payload against a named component (`#/components/schemas/<Shape>` for api.v1,
  `#/$defs/<Shape>` for ws.v1).
- ``fake_meeting_api`` — the downstream the gateway proxies to, SPLIT: ``/transcripts`` +
  ``/meetings`` are served by the REAL, SHIPPED, UNIFIED meeting-api (``meeting_api.create_app``'s
  folded-in collector module, seeded to the api.v1 goldens) so those conformance assertions drive
  shipped meeting-api code; ``/bots*`` stay a golden port-fake (the conformance asserts the frozen
  api.v1 MeetingResponse goldens — the carved ``meeting_api.bot_spawn`` flow has its own tests).
- ``gateway_app`` — `build_gateway()`: constructs the PRODUCTION `gateway.create_app`
  (the shipped app) injected with the split downstream + fake admin-api authorizer, so the
  REST conformance assertions drive shipped code.
- ``ws_harness`` — fakes (FakeWebSocket / FakeRedis) + a `CollectorAuthorizer` whose
  `/ws/authorize-subscribe` hop POSTs the REAL (folded-in) collector; `WSMultiplexHarness.run()`
  drives the production public `gateway.run_multiplex` (subscribe → subscribed ack; forwarded redis
  payload → data frame; malformed → Error).
- ``obs`` — re-exports the production trace emitter (`gateway.obs`) so the tracing eval
  installs its sink on the SAME emitter the shipped app uses.

Import direction: conformance (test) → gateway (prod) + meeting_api (prod). Neither production
package imports anything from conformance.
"""

# Front door (P6): the public submodules. Listed by NAME (not eagerly imported) because each
# submodule pulls in the prod siblings (`gateway` / `transcription_collector`) via the test
# pythonpath — eager import here would couple package init to those being importable. Consumers
# import a submodule directly (`from gateway_conformance.contracts import ...`); `import *`
# resolves these lazily.
__all__ = [
    "contracts",
    "fake_meeting_api",
    "gateway_app",
    "ws_harness",
    "obs",
    "downstream_obs",
]
