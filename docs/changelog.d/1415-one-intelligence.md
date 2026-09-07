- **The in-product meeting copilot is gone; the agent is the one intelligence (#1415).** Vexa no
  longer makes model calls of its own beside the agent. `POST /api/meeting/start` and
  `POST /api/meeting/process` return **404**, `GET /api/models` no longer carries `streaming_model`
  or `meeting_model`, and `meeting_model` is no longer a field on `PUT /user/models` — a value sent
  there is discarded. The live transcript feed is unchanged; to ask about a meeting, ask in the chat.
  See the [Agent API](/api/agent), [Settings](/api/settings) and [Status](/roadmap/status).
