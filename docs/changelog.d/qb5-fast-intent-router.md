- **Simple asks run at once (QB-5).** In your own chat, *call @megan* starts the call and *open the
  OeNB page* opens the page without waiting for a full agent turn. Anything unsure, complex or outbound
  (*invite Ana*, *schedule a call*, anything that sends or publishes) goes to the agent as before, which
  asks first. Each message leaves a route receipt (route, confidence, latency). Behind the
  `intent_router` switch: on with `VEXA_FEATURES=all`, off by `features: {intent_router: off}`. See
  [Chats](/minutes/chats).
