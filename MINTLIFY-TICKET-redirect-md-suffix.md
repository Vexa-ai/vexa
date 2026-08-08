# Mintlify support ticket — `.md` suffix is re-appended *after* a redirect destination's fragment

> **For the founder to file with Mintlify support.** Everything below is verbatim-ready ticket text.
> Diagnosis performed 2026-08-08 against the live site; config referenced is `docs/docs/docs.json`
> on `Vexa-ai/vexa@main` (commit `616778fe`).

---

## Subject

Redirects: on the `.md` content surface, the `.md` suffix is re-appended after the destination's `#fragment`, producing an unusable URL

## Site

`docs.vexa.ai` (custom domain, Vercel-served, `x-vercel-cache` present)

## Summary

When a redirect rule's `destination` contains a URL fragment, requesting the **`.md` variant** of that rule's `source` returns a `Location` header with `.md` appended to the *end of the whole string* — i.e. **after** the fragment — instead of after the path.

The result is a `Location` like `/api/meetings#send-a-bot-to-a-meeting.md`, which is not a markdown URL at all: the browser or agent follows it to the HTML page `/api/meetings` and lands on a non-existent anchor. Every AI client requesting the `.md` surface of a redirected legacy path receives HTML instead of markdown.

## Reproduction

```
$ curl -sI https://docs.vexa.ai/api/bots.md
HTTP/2 307
location: /api/meetings#send-a-bot-to-a-meeting.md
x-matched-path: /_mintlify/_markdown/_sites/[subdomain]/[[...slug]]
```

The relevant rules in `docs.json`:

```jsonc
// index 3
{ "source": "/api/bots",    "destination": "/api/meetings#send-a-bot-to-a-meeting" },
// index 20
{ "source": "/api/bots.md", "destination": "/api/meetings.md" }
```

**Expected:** `location: /api/meetings.md` (rule 20 — an explicit, exact-match rule for this precise source).

**Actual:** `location: /api/meetings#send-a-bot-to-a-meeting.md` — rule 3's destination with `.md` glued on after the fragment.

## All affected paths on our site (probed live, 2026-08-08)

| Requested | `Location` returned | Correct would be |
|---|---|---|
| `/api/bots.md` | `/api/meetings#send-a-bot-to-a-meeting.md` | `/api/meetings.md` |
| `/api/transcripts.md` | `/api/meetings#get-the-transcript.md` | `/api/meetings.md` |
| `/api/recordings.md` | `/api/meetings#recordings.md` | `/api/meetings.md` |
| `/speaker-identification.md` | `/api/meetings#speaker-attributed-transcripts.md` | `/api/meetings.md` |
| `/meeting-ids.md` | `/api/meetings#platforms.md` | `/api/meetings.md` |
| `/token-scoping.md` | `/authentication#scopes.md` | `/authentication.md` |
| `/api-reference/transcription.md` | `/api/meetings#get-the-transcript.md` | `/api/meetings.md` |

All seven return `307` with `x-matched-path: /_mintlify/_markdown/_sites/[subdomain]/[[...slug]]`.

## Why we believe this is suffix handling, not rule ordering

We tested the obvious hypothesis first — that the bare rule is listed before the `.md` twin and wins on
first match — and the evidence rules it out:

1. **`/api-reference/transcription` has no `.md` twin rule at all.** Our `docs.json` contains only the
   bare rule for it. Requesting `/api-reference/transcription.md` *still* produces
   `/api/meetings#get-the-transcript.md`. A rule that does not exist cannot lose an ordering contest —
   so the `.md` handling is happening outside the redirect table entirely.

2. **The `.md` lands after the fragment.** A redirect rule copies its `destination` verbatim. If the
   literal path `/api/bots.md` had merely matched rule 3, the emitted `Location` would be
   `/api/meetings#send-a-bot-to-a-meeting` — with no `.md` anywhere. The fact that `.md` is present,
   and specifically *appended to the tail of the string*, shows it was stripped before the lookup and
   re-attached after it.

3. **`x-matched-path` names the markdown route handler**, not a static path — the request was resolved
   as a markdown-variant request for slug `api/bots`, with the suffix already consumed by routing.

Taken together: the request path has `.md` removed, the **stripped** path is resolved against the
redirect table, and `.md` is then concatenated onto the resulting destination string. Because the
concatenation is string-level rather than path-level, a destination ending in `#fragment` receives the
suffix in the wrong place.

A corollary worth confirming: **explicit `.md` twin rules appear to be dead config.** A rule whose
`source` ends in `.md` can never match a lookup key that has already had `.md` stripped. Our site
carries ~25 such rules; they are currently harmless only because for fragment-less destinations
`destination + ".md"` coincidentally equals the right answer. Please confirm whether these rules are
ever consulted — if not, we would like to know so we can stop maintaining them.

## Impact

Our docs serve an AI-agent audience on the `.md` surface (roughly half of all fetches on affected
paths are non-human). Each of these seven legacy paths is a real, still-trafficked entry point kept
alive deliberately via redirects. Today every agent requesting their `.md` form receives an HTML page
instead of markdown, silently.

The canonical `.md` surface itself is healthy — `curl -sI https://docs.vexa.ai/api/meetings.md`
returns `200` with `content-type: text/markdown; charset=utf-8` — so the defect is confined to
redirect resolution.

## What we are asking for

Re-attach the `.md` suffix to the **path component** of the destination, before any `#fragment` —
so `/api/meetings#send-a-bot-to-a-meeting` + `.md` yields `/api/meetings.md`, discarding or preserving
the fragment as appropriate rather than corrupting it.

Alternatively, consult exact-match `.md` rules before applying suffix-stripping, which would let us fix
this ourselves in `docs.json`.

If neither is on the near-term roadmap, please tell us — our only remaining workaround is to strip the
fragments from those seven destinations, which would degrade deep-linking for human readers to fix the
agent surface. We would rather not make that trade silently.

---

## Local workaround note (not part of the ticket)

If the vendor cannot fix this, the in-repo mitigation is to drop the `#fragment` from the destinations
of the seven rules above. That makes the `.md` surface correct (`/api/meetings.md`) at the cost of
sending human readers to the top of `/api/meetings` instead of the right section — a real regression
against the intent of #1045, which added those fragments deliberately. **This is a trade-off for the
founder to decide; it has not been applied.**
