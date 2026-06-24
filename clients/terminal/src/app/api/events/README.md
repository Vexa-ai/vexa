# /api/events

Proxy → agent-api `POST /events` — the generic event-source ingress (an `event.v1` Event → a `unit.v1` Invocation → the one Dispatcher). The terminal posts events here (e.g. a triage trigger); agent-api stays tool-agnostic.
