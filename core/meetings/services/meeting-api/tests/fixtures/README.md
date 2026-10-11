# tests/fixtures — synthetic row corpora

Data files the L1 evals drive through pure functions. **Synthetic by contract**: no customer
identifiers of any kind — no emails, no user or account ids, no meeting URLs, no real meeting ids.
Where a file replicates a shape observed in production, it replicates the *shape* (the field
combination that makes the row wrong) and nothing else. Tests assert that property directly, so a
leak fails the suite rather than shipping.

| File | Driven by | Carries |
|---|---|---|
| `claims_audit_rows.json` | `test_claims_audit.py` | 16 terminal/in-flight meeting rows for the lifecycle claims audit (`Vexa-ai/vexa#1191`) — the three inconsistent shapes live in prod (`left_alone`@`awaiting_admission`, `awaiting_admission_timeout`@`joining`, a `completed` deaf run), one row per remaining invariant branch, and the clean rows that must stay clean, including both threshold boundaries. Also the input to `python -m meeting_api.lifecycle.claims_audit`. |

Each row carries a `_note` describing what it replicates and why it does or does not fire; the
auditor ignores unknown keys. Expected findings live in the test module, not here, so the file
stays a plain rows file the CLI can read.
