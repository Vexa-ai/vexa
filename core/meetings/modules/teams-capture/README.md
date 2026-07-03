# @vexa/teams-capture — MS Teams' contribution to the mixed lane (browser)

_meetings/ · module · Teams page → `mixed-capture.v1` hints (the WHO signal) + chat._

Runs **inside the meeting page**. Like Zoom, Teams delivers one mixed audio stream (captured by
[`@vexa/mixed-capture-core`](../mixed-capture-core/)), so this brick provides only the **WHO** signal —
no audio of its own:

- `createTeamsSpeakers` — watches Teams' voice-level "blue-square" outline
  (`[data-tid="voice-level-stream-outline"]` + `vdi-frame-occlusion` on it or an ancestor = actively
  speaking; NO caption dependency) and emits debounced speaking start/stop per participant → a
  `mixed-capture.v1` **hint** (kind `dom-outline`). A ~2 s heartbeat re-asserts the current speaker so a
  consumer that started mid-turn learns who's talking without waiting for the next transition. This
  module OWNS the Teams speaker-detection selectors — it also exports `teamsParticipantSelectors`,
  `teamsNameSelectors`, `teamsParticipantIdSelectors`, `teamsMeetingContainerSelectors` (single source;
  the bot's `selectors.ts` re-exports from here).
- `createTeamsChat` — reads the chat panel (content tier); emits each new message as `{ sender, text }`.

**Two hosts, one brick** — the [bot](../../services/) instantiates it in a `page.evaluate` (bundled
into its browser globals); the [extension](../../../clients/) imports it on Teams hosts to label the
mixed `tabCapture` track. Selectors are defensive (Teams' `data-tid` DOM shifts across builds).

## Surface
`createTeamsSpeakers` · `createTeamsChat` · the shared selector arrays (`teamsParticipantSelectors`,
`teamsNameSelectors`, `teamsParticipantIdSelectors`, `teamsMeetingContainerSelectors`) (+ types
`TeamsSpeakers`, `TeamsSpeakersOptions`, `TeamsSpeakerIdentity`, `TeamsChat`, `TeamsChatMessage`).
Front door: [`src/index.ts`](src/index.ts).

## Verify
`pnpm --filter @vexa/teams-capture run build` — `tsc` clean. The DOM scraping (blue-square detection +
chat) is validated **live** in a real Teams (extension/bot) — consistent with how the lane has always
been tested. `tsconfig` adds the `DOM` lib. Covered by `gate:node`, `gate:isolation`, `gate:exports`,
`gate:readme`.
