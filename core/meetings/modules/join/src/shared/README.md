# join/src/shared — cross-platform join helpers

`escalation.ts` — `startDebugView`: opens the live debug lens (VNC pixels on Linux, CDP
control anywhere) so a human/agent can see and unblock a stuck join (e.g. a reCAPTCHA
block). Imports host symbols from `../_host` and Node builtins only.

`admission.ts` — `AdmissionError`, the typed admission outcome every platform throws.

`ui-locale.ts` — the English pin (`PINNED_UI_LOCALE = "en-US"`, used by `browser-args.ts` and
Meet's `hl=`) and its check. `checkUiLocale` reads the meeting page's language after load
(`<html lang>`, else an English landmark in the visible text) on every platform and logs a
non-English page as `ui_locale_mismatch`; `joinMeeting` then throws a failed join on that page as
`UiLocaleMismatchError` (an `AdmissionError`, outcome `join_failure`), because the English
waiting-room and rejection detectors cannot read it.
