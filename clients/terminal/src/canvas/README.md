# canvas

Harnessed Meeting Canvas runtime for terminal-side generated React views.

Public surface:
- `runtime.tsx` shows a `views/meeting.tsx` view WITHOUT running it on the terminal page: it mounts
  `/canvas-frame` (`app/canvas-frame/page.tsx`) in an iframe sandboxed to `allow-scripts`, posts the
  source and the meeting in, and runs only the actions the frame posts back (`frame/protocol.ts`).
  The frame document has its own policy (`canvasFrameContentSecurityPolicy`): an opaque origin that
  connects nowhere. `frame/compile.ts` is the one module that turns a view's text into code, and the
  frame (`frame/CanvasFrame.tsx`) is the only thing that imports it. `validator.ts` is the author's
  feedback, not the boundary.
- `kit.tsx` exports the theme-locked `ui` component vocabulary.
- `useMeeting.ts` and `actions.ts` expose the live meeting feed and sanctioned side effects.

This folder may depend on terminal surfaces for existing meeting/workspace data seams, but generated
views may only use the injected harness globals and `ui.*` kit.

- `MeetingView.tsx` is the one meeting view, the meeting sidebar: tools on top (`MeetingViewHeader.tsx`:
  name, status, time, participants, Play recording · Share · Update notes · "…", the recording player),
  the note in the middle (rendered, edited in place), and the transcript as a resizable bottom drawer.
  `meetingNotes.ts` holds its rules: where the note comes from, Create and Update through the existing
  routes, the collapsed transcript tail. `MeetingChip.tsx` is the "Meeting: name · open" chip a page
  that names a meeting shows at its top. The registered `meeting` tab (`surfaces/meeting.tsx`) renders it, so the Minutes
  pages panel, the Calls list and `/?meeting=` links all show the same view.

It imports from `minutes/` (`minutes/meetingPlayback`, `minutes/RecordingPlayer`, `minutes/MeetingControls`,
`minutes/meetingNote`, `minutes/meetingActions`, `minutes/meetingDeletion`, `minutes/CallName`,
`minutes/MarkdownEditor`, `minutes/workspaceReadme`, `minutes/extend`, `minutes/ExtendAction`), and
`minutes/` does not import it. The window-event names and
chat-turn marks both folders use live in `platform/` (`events.ts`, `turnMarks.ts`), below every surface.
`scripts/check-isolation.js` refuses a pair of `src/` folders that import each other unless the pair is
on its list of known cycles, and canvas ⇄ minutes is not on it.

The canvas sits ABOVE `surfaces/`: it reads the surfaces' meeting stores (`useMeeting.ts`), and no
surface imports it. Its views register by kind in `register.tsx` — the `canvas` tab, the meeting
tab's canvas body (`ui-kit/canvasSlot.ts`) and the in-page transcript widget (`ui-kit/transcriptSlot.ts`)
— and the composition root (`app/App.tsx`) imports that file beside the surfaces barrel. The
`terms` window event and the minutes greetings the chat surface uses live in `platform/`.
