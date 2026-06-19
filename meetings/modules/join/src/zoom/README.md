# join/src/zoom — Zoom **web client** join flow

Enter a Zoom meeting via the **web client only** (`buildZoomWebClientUrl` → join). No
native Zoom SDK (proprietary, Cat-X under P17 — deliberately not promoted). `join.ts`,
`admission.ts`, `leave.ts` (popup dismissal), `removal.ts`, `selectors.ts`. Imports host
symbols from `../_host`, `playwright`, and Node builtins only.
