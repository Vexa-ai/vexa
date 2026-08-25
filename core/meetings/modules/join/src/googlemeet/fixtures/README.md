# fixtures — captured platform pages

**Everything in this directory is a REAL capture.** That is the whole point of it existing.

Every other Google Meet DOM in this module is fabricated, and says so:
`join-cta.test.ts` opens with *"FIXTURE HONESTY (#857): the lobby DOMs below are
FABRICATED, not captured."* A fabricated fixture proves the location logic against a real
DOM engine; it cannot prove Google's page has that shape. This directory is where the
second kind of proof lives.

## The rule

A file lands here only if it was served by the platform and captured through
[`../../../scripts/capture-page-dom.ts`](../../../scripts/capture-page-dom.ts), which runs
inside the hot debug container (`Dockerfile.debug` — Xvfb, humanized X11, the stealth
chromium the bot actually joins with). Capturing through a developer's desktop browser
does not count: the page a bot is served is not always the page a person is served.

Each capture is a pair:

| file | holds |
|---|---|
| `<name>.html` | the relevant **subtree**, verbatim, under a comment header recording provenance and what the capture proves |
| `<name>.meta.json` | requested/final URL, HTTP status, title, `html.lang`, `navigator.language`, body text, button labels, the console lines, and which URLs it reproduced on |

Nothing is edited, reordered or invented — if a capture needs explaining, the explanation
goes in the header comment, never in the markup.

## Sanitizing

Capture the subtree that matters, not the document: a full Meet page is ~2.4 MB, almost
all of it script. Check that what you commit contains no `<script>`, no `<style>`, and no
token-shaped strings. The capture script never reads cookies, storage or headers, so the
only way secrets get in here is by committing more of the page than the fixture needs.

## Inventory

| fixture | captured | what it is |
|---|---|---|
| `gmeet-404-meeting-not-found` | 2026-08-25 | Google Meet's "Check your meeting code" screen for a meeting space that does not exist (`data-startup-code="217"`). Consumed by [`../meeting-not-found.test.ts`](../meeting-not-found.test.ts). Filed as [#1325](https://github.com/Vexa-ai/vexa/issues/1325). |

Still uncaptured, in the order they are worth having: a real Meet lobby (which is what
[#857](https://github.com/Vexa-ai/vexa/issues/857) actually asks for and would let the
fabricated `join-cta.test.ts` DOMs be replaced), a real host denial, and the Zoom LFX
white-label wrapper ([#1261](https://github.com/Vexa-ai/vexa/issues/1261)). The first two
need a second Google account willing to host and not admit; the third needs only a URL.
