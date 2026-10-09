# canvas

Harnessed Meeting Canvas runtime for terminal-side generated React views.

Public surface:
- `runtime.tsx` renders validated `views/meeting.tsx` source.
- `kit.tsx` exports the theme-locked `ui` component vocabulary.
- `useMeeting.ts` and `actions.ts` expose the live meeting feed and sanctioned side effects.

This folder may depend on terminal surfaces for existing meeting/workspace data seams, but generated
views may only use the injected harness globals and `ui.*` kit.

It imports from `minutes/` (`minutes/meetingPlayback`, `minutes/RecordingPlayer`, `minutes/MeetingControls`,
`minutes/extend`, `minutes/ExtendAction`), and `minutes/` does not import it. The window-event names and
chat-turn marks both folders use live in `platform/` (`events.ts`, `turnMarks.ts`), below every surface.
`scripts/check-isolation.js` refuses a pair of `src/` folders that import each other unless the pair is
on its list of known cycles, and canvas ⇄ minutes is not on it.
