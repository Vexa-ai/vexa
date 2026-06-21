# acts.v1 — the bot command bus

Control-plane → bot, over redis pub/sub on **`bot_commands:meeting:{meeting_id}`**. One JSON message per
command, discriminated by `action`; **unknown actions are ignored** (forward-compatible).

## Commands
- **Core control** (always honored): `leave` · `reconfigure` (language/task/allowedLanguages).
- **Voice agent** (optional, gated by `voiceAgentEnabled` in the invocation): `speak` · `speak_audio` ·
  `speak_stop` · `chat_send` · `chat_read` · `screen_show` · `screen_stop` · `avatar_set` · `avatar_reset`.

`Act` is the `oneOf` of all variants (`$defs`). No auth token (transport-layer), no tenancy fields.
Goldens (`Act.<case>.json`) validate against `#/$defs/Act` via `gate:schema`.
