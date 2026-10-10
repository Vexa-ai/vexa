# api/meeting/bundle-parts

`GET /api/meeting/bundle-parts?meeting_id=` — the agent domain's half of a meeting export (the bound
workspace's tree and the meeting's page) as a zip, streamed through untouched from the gateway's
`/agent/meeting/bundle-parts`. `204` when the meeting has neither. Refused (404) in meetings-only mode.
