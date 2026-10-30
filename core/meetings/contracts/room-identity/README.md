# room-identity — what counts as a meeting-room device

`room-patterns.json` is the **single source of truth** for the heuristic that decides whether a
meeting participant is a **room device** or a **person**, from the display name the platform gives
us. It is data, not a schema: no `*.schema.json`, so it is outside the contract seal — it is
expected to grow as we meet more hardware.

## Why the classification exists

A room system mixes every microphone **inside the room hardware**, before the call. What joins the
meeting is one participant, one audio stream, one display name — so every human in that room is
attributed to the room's name. Google's own Meet transcripts do the same thing: in-room speech is
attributed to the room device's robot account.

Rung 1 does not try to separate those voices. It makes the existing answer **legible**: the speaker
is a room, and the data now says so, instead of a downstream consumer reading a device name as a
person's name.

## The three values

| `kind` | Meaning |
|---|---|
| `room` | **Evidence.** A pattern in this file matched the display name. |
| `person` | **A default, not evidence.** A resolved display name with no room marker. |
| `unknown` | No name to classify — the binder refused, or the label is provisional. |

`person` is the honest weak value. A room kit named after a human — we have seen a real one called
`Steve Jobs` — reads as `person` and always will, because nothing in the data distinguishes it. The
fix for such a kit is one admin-console edit on the customer's side; a kit named `Amsterdam — Room 2`
produces transcripts that say `Amsterdam — Room 2`, and is detected.

## Two implementations, one table

The table is embedded in each language rather than read from disk at runtime, because the bot and the
meeting-api ship as separate images and neither mounts this directory:

- TypeScript — `core/meetings/services/bot/src/room-identity.ts`
- Python — `core/meetings/services/meeting-api/src/meeting_api/collector/room_identity.py`

Each carries a **drift test** that reads this file and fails if its embedded table differs. Change
the JSON and both tests go red until both implementations follow.

## Runtime override

`VEXA_ROOM_PATTERNS` takes a JSON array of regex strings and **replaces** the table. Make the first
element `"+"` to **append** to the defaults instead:

```
VEXA_ROOM_PATTERNS='["+", "\\bvergaderruimte\\b", "^HQ-"]'
```

A malformed value is ignored and the defaults stand — a bad env var must never take a running bot
off the air.
