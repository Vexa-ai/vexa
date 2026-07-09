# join/src/googlemeet — Google Meet join flow

Enter a Google Meet and resolve admission. `join.ts` (name entry + ask-to-join, humanized
or synthetic input), `admission.ts` (the lobby→admitted/rejected oracle, incl. bot-block
detection), `leave.ts`, `removal.ts` (kicked-out monitor), `selectors.ts` (DOM selectors).
`humanized/` holds the X11 mocap-driven input. Imports host symbols from `../_host`,
`playwright`, and Node builtins only.
