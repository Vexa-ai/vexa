# @vexa/teams-capture

MS Teams' contribution to the **mixed lane**. Teams delivers one mixed audio
stream (captured by `@vexa/mixed-capture-core`); this module provides the **WHO**
signal:

- `createTeamsSpeakers` — watches Teams' voice-level "blue-square" outline to
  detect the active speaker → a `mixed-capture.v1` **hint** (kind `dom-outline`).
  Also exports the shared Teams DOM selectors (`teamsParticipantSelectors`, …).
- `createTeamsChat` — reads the chat panel (content tier).

## Files
`src/msteams-speakers.ts`, `src/teams-chat.ts`. Zero external imports (pure DOM);
`npm run check:isolation` enforces it.
