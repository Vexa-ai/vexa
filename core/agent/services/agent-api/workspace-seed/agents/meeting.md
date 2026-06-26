---
enabled: true
model: openrouter/free               # must be in the allowlist; else falls back to the default + logs
cadence_segments: 4                  # run a copilot beat every N completed segments (or on a new speaker)
card_kinds: [person, topic, action]
write_meeting_doc: true              # author the post-meeting kg entity on session_end
---
<!-- Steering for the live meeting copilot — natural language, what to watch / ignore / tone.
     This whole body is merged into the copilot prompt. Edit it to tune behavior. -->
Watch for people, companies, decisions, and action items. Keep it neutral and concise.
