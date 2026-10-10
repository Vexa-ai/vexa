# api.v1 goldens — example messages (the spec)

One `<Shape>.<case>.json` per public response shape; `validate.mjs` checks each against the
sealed `#/components/schemas/<Shape>` in `../api.schema.json`. The goldens ARE the spec — if
an example can't express it, the surface doesn't carry it. Current shapes: `MeetingResponse`,
`MeetingListResponse`, `TranscriptionResponse`, `TranscriptionSegment`, `BotStatusResponse`,
`AuthMeResponse` (`/auth/me`, whose `is_admin` the MCP reads before it spends the operator key).
`MeetingResponse.deleted.json` / `.deleting.json` carry `data.artifact_deletion` (`ArtifactDeletion`)
in both its states; agent-api's and the terminal's deletion readers are tested against them.
