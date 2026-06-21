# api.v1 goldens — example messages (the spec)

One `<Shape>.<case>.json` per public response shape; `validate.mjs` checks each against the
frozen `#/components/schemas/<Shape>` in `../api.schema.json`. The goldens ARE the spec — if
an example can't express it, the surface doesn't carry it. Current shapes: `MeetingResponse`,
`MeetingListResponse`, `TranscriptionResponse`, `TranscriptionSegment`, `BotStatusResponse`.
