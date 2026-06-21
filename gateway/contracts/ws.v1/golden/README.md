# ws.v1 goldens — example messages (the spec)

One `<Shape>.<case>.json` per WebSocket message; `validate.mjs` checks each against
`#/$defs/<Shape>` in `../ws.schema.json`. These are the exact messages vexa main's G5
WebSocket gate test exchanges: `SubscribeRequest`/`UnsubscribeRequest` (client→server),
`Subscribed` + `Error` (control), and the live data messages `TranscriptionSegment`,
`BotStatus`, `ChatMessage`. The goldens ARE the spec.
