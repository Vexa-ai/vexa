# Lite lane — human eyeball verdict: BASIC functionality

**Pack:** pack-msteams-diarization-cutover (#394)
**Lane:** Lite (single-container, pack ports 42310-42312)
**Status:** PENDING OPERATOR

## What this verdict covers

Whether the **overall user-facing surface** in the Lite lane behaves
correctly with the bot image built from this pack branch. This is
independent of the pack's specific blast-radius — it's the
"sign-in still works, meeting list loads, transcript surface is
reachable" check.

## Checklist for the operator

| step | URL / action | expected | observed | OK? |
|---|---|---|---|---|
| Dashboard loads | `http://localhost:42310/` | dashboard UI renders, no console errors | _operator to fill_ | _( )_ |
| Sign-in | use admin API token | logged-in dashboard with empty meeting list | _operator to fill_ | _( )_ |
| API docs | `http://localhost:42311/docs` | Swagger renders, /v1/bots endpoint visible | _operator to fill_ | _( )_ |
| MCP endpoint health | n/a (single-container Lite has no MCP host port by default) | 200 OK | _operator to fill_ | _( )_ |
| Trigger Teams bot | POST /v1/bots with operator-approved Teams URL | 200 OK + bot container starts | _operator to fill_ | _( )_ |
| Bot joins meeting | visible in Teams UI | bot avatar in attendee list within 60s | _operator to fill_ | _( )_ |
| Transcript appears | meeting transcript page in dashboard | text segments stream in during meeting | _operator to fill_ | _( )_ |
| Bot leaves cleanly | stop bot via API | bot leaves, container exits 0 | _operator to fill_ | _( )_ |

## Verdict template

```
Verdict: [ pass | pass with notes | changes requested | block ]
Reviewer: <name / email>
Timestamp: <ISO-8601>
Notes: <any divergence from expected>
```

(Verdict to be filled by an operator. The develop skill cannot
self-grant this.)
