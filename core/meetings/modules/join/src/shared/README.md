# join/src/shared — cross-platform join helpers

`escalation.ts` — `startDebugView`: opens the live debug lens (VNC pixels on Linux, CDP
control anywhere) so a human/agent can see and unblock a stuck join (e.g. a reCAPTCHA
block). Imports host symbols from `../_host` and Node builtins only.
