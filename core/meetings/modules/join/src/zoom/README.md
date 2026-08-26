# join/src/zoom — Zoom **web client** join flow

Enter a Zoom meeting via the **web client only** (`buildZoomWebClientUrl` → join). No
native Zoom SDK (proprietary, Cat-X under P17 — deliberately not promoted). `join.ts`,
`admission.ts`, `leave.ts` (popup dismissal), `removal.ts`, `selectors.ts`, `session.ts`.
Imports host symbols from `../_host`, `playwright`, and Node builtins only.

`session.ts` is the **dead-profile guard** (#1061): in authenticated mode it decides whether
the restored Zoom profile is still signed in, and throws the typed, PERMANENT
`auth_session_missing` when it is not — a state that otherwise surfaces minutes later as a
nameless join-button timeout. Detection only; re-authenticating the profile is follow-up work.
