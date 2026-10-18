- **Calls: the call dock is one compact bar.** At the top of the conversation column: the call's one
  name (never "Call · New chat"), the time, who is in it with a ring on whoever speaks, Vexa as a small
  listening mark, and icon controls with tooltips — mic, camera, screen share, "…" (devices, notes and
  transcript, add people) and Leave. Camera and screen-share tiles open under the bar while one is on.
  The transcription notice is one quiet line before joining. The "Share to the room" box is gone:
  writing to the room is the chat's own composer.
- **Calls: the transcriber starts and ends with the call.** When the last person presses Leave, the
  call's transcriber leaves within seconds and the meeting completes; a dropped connection still gets
  the grace window to come back to the same call. A call never offers Stop bot or Send bot again.
- **Calls: an incognito chat's call starts no transcriber.** No listener joins its room and no meeting
  is made (with `CALL_TRANSCRIPTION_SWITCHABLE=true`; otherwise the call is refused). A call nobody
  joins now ends after `CALL_FIRST_JOIN_TIMEOUT_SEC` = 30 s by default (was 300), so a started and
  cancelled call does not keep its transcriber waiting.
- **Calls: show a page to everyone** (feature `call_show`). In a call, a page of a shared workspace can
  be shown to everyone: it opens in each participant's panel ("Shown by …"), everyone follows the
  presenter's scroll and selection (highlighted and scrolled into view), and anyone can stop following.
  Only a page every person in the chat can read is shown; otherwise sharing the workspace is offered
  first. The agent's tools: `call_show`, `call_highlight`. Nothing is stored but one line in the chat.
