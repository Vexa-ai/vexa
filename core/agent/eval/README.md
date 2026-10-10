# agent-api eval

Offline evaluation assets for agent-api behavior. Replay fixtures and conversion tools live under `replay/`.

## Chat cases (`chat_eval.py`, `chat_cases/`)

Short recorded chats that end on the person's message, scored on the model's next step against any
OpenAI-compatible endpoint, by hand (`chat_eval.py <case>`; see its docstring). The tools are the ones
agent-api serves, with their served descriptions, and the system text is the guidance every dispatch
carries. `tests/test_chat_eval.py` holds the cases and the scorer offline.

- `connect-gmail` — a Gmail tool failed (`reconnect_required`, or `store_unavailable`); the person says
  "let's connect gmail". Passing means calling `connection_request`, or (for an outage) saying it is an
  outage that reconnecting will not fix. Retrying the failed tool fails the case.
