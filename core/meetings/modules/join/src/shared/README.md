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

`url-redaction.ts` — `redactMeetingUrl` / `redactUrlsIn`: every log line and error in this brick
that names a URL names it with its secret query and fragment values masked (Zoom `pwd`, Teams `p`,
Jitsi `jwt`, Zoom `zak`, any `*key*`/`*secret*`/`*token*`), host, path and meeting id kept.
`rethrowRedacted` is the `page.goto(...).catch(...)` that redacts Playwright's own message, which
quotes the URL. Exported as `@vexa/join/url-redaction` for the bot. Held, with the vendored Python
`meeting_url_redaction.py`, to `meeting-url-redaction-vectors.json` (gate:fact-parity).
