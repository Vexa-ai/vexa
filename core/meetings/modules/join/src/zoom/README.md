# join/src/zoom — Zoom **web client** join flow

Enter a Zoom meeting via the **web client only** (`buildZoomWebClientUrl` → join). No
native Zoom SDK (proprietary, Cat-X under P17 — deliberately not promoted). `join.ts`,
`prejoin-state.ts` (what the page shows before the name field), `admission.ts`, `leave.ts` (popup dismissal), `removal.ts`, `incoming-video.ts`, `selectors.ts`. Imports host
symbols from `../_host`, `playwright`, and Node builtins only.

Before the name field the page is read on every poll until it settles (`prejoin-state.ts`): Zoom
replaces the first page with `…/join?wpk=…`, and what lives there can be the pre-join, the "Error -
Zoom" host-not-started page, a sign-in page, or a self-refreshing hold page ("The host has another
meeting in progress"). A hold is waited out within the 10-min host budget (then `zoom_prejoin_held`,
outcome `lobby_timeout`); a page that is none of these for 30 s ends as `name_input_missing`
(outcome `join_failure`) with the page's URL, title and text in the message.

`incoming-video.ts` (`stopZoomIncomingVideo`) turns participants' video off once the bot is in the
call, through the footer More menu's "Stop Incoming Video", and confirms the item then reads "Start
Incoming Video". The bot needs only the audio and the active-speaker tile; each camera that is on
otherwise costs a software video decode inside the bot's CPU limit. The active-speaker containers
and their name footers stay in the DOM with video off. The bot calls it after admission unless the
invocation sets `videoReceiveEnabled: true`.
