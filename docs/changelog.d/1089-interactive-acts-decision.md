- **Interactive meeting capabilities get one API home — and the status page stops overstating speak (#1089).**
  [ADR-0035](https://github.com/Vexa-ai/vexa/blob/main/docs/adr/0035-interactive-capabilities-live-behind-one-act-endpoint.md)
  settles where speak / chat / screen / avatar live: a single `POST /bots/{platform}/{native_meeting_id}/acts`
  endpoint carrying an `acts.v1` act, with `POST`/`DELETE …/speak` and `POST …/chat` kept as aliases for
  callers already coding against them; `…/screen` and `…/avatar` retire from `api.v1`. The
  [status page](/roadmap/status) is corrected in the same change: a bot spawned through the public API
  cannot speak today even once the route lands, because `voice_agent_enabled` is accepted by `POST /bots`
  and silently dropped before the invocation is built. See [Roadmap status](/roadmap/status).
