# canvas

Harnessed Meeting Canvas runtime for terminal-side generated React views.

Public surface:
- `runtime.tsx` renders validated `views/meeting.tsx` source.
- `kit.tsx` exports the theme-locked `ui` component vocabulary.
- `useMeeting.ts` and `actions.ts` expose the live meeting feed and sanctioned side effects.

This folder may depend on terminal surfaces for existing meeting/workspace data seams, but generated
views may only use the injected harness globals and `ui.*` kit.

It imports from `minutes/` today, and these are the whole list: `minutes/meetingPlayback` and
`minutes/RecordingPlayer` (`LiveTranscriptEngine.tsx` — the playback clock), `minutes/MeetingControls`
(`MeetingCanvasView.tsx`), `minutes/extend` (`TranscriptTermControls.tsx`) and `minutes/ExtendAction`
(`TranscriptExtend.tsx`). `minutes/` imports `canvas/` too, so the two folders form a cycle that no
gate checks yet; a new import from `minutes/` widens it and belongs in this list.
