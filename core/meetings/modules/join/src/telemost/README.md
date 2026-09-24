# join/src/telemost — Yandex Telemost join flow

Enter a Yandex Telemost meeting (`https://telemost.yandex.ru/j/<id>`, or the Yandex 360 host
`telemost.360.yandex.ru`) as a guest. `buildTelemostMeetingUrl` canonicalises the link to
origin + `/j/<id>` and refuses anything else; the host is never rewritten.

The page is a messenger shell; the call itself (pre-join card, waiting room, call screen) renders
in a same-origin iframe at `/private-join/<id>`. `callFrame(page)` finds it (exported as
`telemostCallFrame` for embedders, whose page-side capture must run there — the WebRTC peers and
`<audio>` elements live in that frame). The shell carries the announcement modals and the
"meeting not found" page, so text verdicts scan both documents.

Telemost exposes no runtime API on the page, so every verdict is read from the DOM:
admitted = the call's leave control is visible with no pre-join form and no waiting-room
text. A waiting-room decline throws `AdmissionError("denial")`, a give-up
`AdmissionError("lobby_timeout")`. UI strings are matched in Russian and English.

`join.ts` (URL builder, app-or-browser choice, guest name, join click), `admission.ts`,
`leave.ts`, `removal.ts`, `selectors.ts` (the only file that knows the Telemost DOM),
`join.test.ts` (URL golden), `admission.test.ts` (fabricated-page oracle + typed outcomes).
Imports host symbols from `../_host`, `playwright`, and `../shared/*` only.
