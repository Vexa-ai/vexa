- **Transcript signals: the `signals` service (#1783).** A new optional service listens to live
  meetings with a small model the company picks (`model_signals` in `_global/POLICIES.md`: Typesafe
  Jev directly or through OpenRouter, or any OpenAI-compatible endpoint), scores each confirmed line
  against the listening brief the meeting's agent wrote, and wakes the agent through the company's
  `meeting.signal` flow. Off by default: Compose profile `signals`, Helm `signals.enabled`, and it
  calls no model until `transcript_signals: on`. Caps on wakes and model cost per meeting. The model's
  key is a company secret in the credential broker (new role `signals`,
  `python -m credential_broker.signals_key put <name>`). Rooms (chats with two or more people) use
  the same listener: rule triggers (`@vexa`, a reply to the agent, the agent's own keywords, people
  and patterns) wake the agent at once, every other message is scored by the cheap model. See
  [Transcript signals](/transcript-signals).
