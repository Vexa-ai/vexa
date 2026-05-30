# Human overall-functionality verdict

**Pack:** pack-msteams-diarization-cutover (#394)
**Status:** PENDING OPERATOR

## What this covers

The single, cross-lane "is everything still working at a high level"
verdict. This is distinct from the per-lane `human-eyeball-basic.md`
files (which capture per-lane basic verdicts) and from
`human-eyeball-blast-radius.md` (which capture per-lane pack-specific
verdicts).

This is the place to record:
- whether the operator could sign in, list meetings, and surface
  transcripts in BOTH Compose and Lite;
- whether any non-Teams behaviour (Google Meet, Zoom — if applicable
  on this branch) still works;
- whether any user-visible regression appeared that the per-lane
  checklists missed.

## Operator template

```
Verdict: [ pass | pass with notes | changes requested | block ]
Reviewer: <name / email>
Timestamp: <ISO-8601>
Compose URLs reviewed: <list>
Lite URLs reviewed: <list>
Notes:
  - <observations>
```

(Pack scope is MS Teams-specific; Google Meet and Zoom paths should
be unchanged. Confirm by spot-check.)
