# minutes/callkit

The call's presentational pieces: props in, callbacks out, no fetch and no media stack.

- `CallBar.tsx` — the call dock: one compact bar at the top of the conversation column (the call's
  one name, the time, who is in it with a ring on whoever speaks, Vexa as a small listening mark, the
  profile) and icon controls (mic, camera, screen share, "…", expand, Leave). Tiles open under it only
  while a camera or a shared screen is on. `CallJoinBar` is the same line before joining, with the
  quiet "transcribed" notice.
- `People.tsx` — avatars, the roster line, video tiles, Vexa's mark.
- `Stage.tsx` — tile layouts: one other person with a self-view, 2×2, 3×2, 3×3 with "+N", a shared
  screen with a filmstrip.
- `types.ts` — the shapes (`CallPerson`, `ScreenShare`) and `clock`.

`../CallRoom.tsx` maps the LiveKit room onto these; the UI lab maps its scripted room onto the same
components. No transcript is drawn here: it lives in the meeting sidebar's drawer.
