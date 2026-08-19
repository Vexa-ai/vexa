- **A running bot's transcription language and task can be changed mid-meeting (#516).**
  `PUT /bots/{platform}/{native_meeting_id}/config` was sealed in `api.v1` and forwarded by the
  gateway, but nothing served it — the call returned `404` and the only recourse was to stop the bot
  and spawn a new one, losing the meeting record's continuity. meeting-api now serves it: the route
  publishes an `acts.v1` `reconfigure` on the bot's command channel and persists the new values on
  the meeting record, and the running bot applies them to its live STT config so the **next**
  transcription request carries the new `language` / `task` — no restart, no respawn. Omitted fields
  are left alone; `null` clears a pin back to auto-detect. `task: "translate"` now reaches the
  transcription service, which has always accepted it. See
  [Meetings API](/api/meetings#change-a-running-bots-language-or-task) and
  [Send a bot](/how-to/send-a-bot).
