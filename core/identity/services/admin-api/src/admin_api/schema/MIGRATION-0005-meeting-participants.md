# MIGRATION-0005 — `meeting_participants` (the roster layer of the meeting record)

**Status:** new table added to the SSOT model (`schema/models.py`) and to the meeting-api mirror
(`meeting-api/.../sessions/models.py`). It is a **fresh table** — `ensure_schema`'s
`create_all(checkfirst=True)` builds it on every environment, including populated ones, with no
backfill and no lock on an existing hot table. There is **no pre-deploy ops step** (unlike
MIGRATION-0002, which added an index to the populated `meetings` table).

**Cross-domain:** the identity domain owns the schema SSOT + convergence
(`admin-api/.../schema/`); the meeting domain owns the mirror and the routes that read and write
this table (`meeting-api/.../collector/`). Both files carry the same definition.

## What the table is

```sql
CREATE TABLE meeting_participants (
    id          SERIAL PRIMARY KEY,
    meeting_id  INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    email       VARCHAR(320),
    name        VARCHAR(255),
    role        VARCHAR(32),
    source      VARCHAR(16) NOT NULL,
    joined_at   TIMESTAMP,
    left_at     TIMESTAMP,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMP DEFAULT now(),
    updated_at  TIMESTAMP DEFAULT now()
);

CREATE INDEX ix_meeting_participants_meeting_id ON meeting_participants (meeting_id);
CREATE INDEX ix_meeting_participant_email_lower ON meeting_participants (lower(email));
CREATE UNIQUE INDEX uq_meeting_participant_identity
    ON meeting_participants (meeting_id, source, lower(email))
    WHERE email IS NOT NULL;
```

## Why a table and not another `meetings.data` key

Three of the four things this record must do are not expressible in the JSONB blob at the cost the
product needs:

1. **`GET /meetings?participant=<email>` must be a predicate, not a scan.** "Every meeting with
   anyone `@example.com`" is the query that unblocks meeting→CRM automation, and the existing
   whole-column `ix_meeting_data_gin` cannot serve a case-insensitive email probe inside a nested
   array. `lower(email)` on a narrow table can.
2. **A participant has its own lifetime** — `joined_at` / `left_at` are per-person, per-run facts a
   platform reports independently of the meeting row's own timestamps.
3. **`meetings.data` is already the thing that caused an outage.** The list view had to learn to
   drop heavy `data` keys (`speaker_events`, `bot_logs`, …) after a 4.6 MB list response wedged the
   event loop for ~1.5 h (see `collector/projection.py`). A roster that grows with attendee count
   belongs outside the blob that the list has to project away.

## What is deliberately NOT in this table

**No speaker↔participant mapping.** There is no `speaker_label` column, no join table, no
confidence score, and no resolver anywhere in this change. Identity in this product is three
things — a **participant** is an email on an invitation, a **speaker** is an attributed voice in
`transcriptions.speaker`, a **user** is someone with sessions — and joining the first two is an
**agentic job done with the roster and the record in context, not a system one** (founder ruling).
The record therefore carries both layers side by side and honestly: unattributed speech stays
unattributed, and a consumer that wants a mapping produces one itself, with the evidence visible.

## Relationship to the stores that already exist

Nothing is migrated, and nothing that writes today is rewired:

| Existing store | Written by | What happens now |
|---|---|---|
| `meetings.data['attendees']` — `[{email, name?, partstat?}]` | `calendar_sync` (an .ics feed's ATTENDEE lines) | unchanged; **projected read-only** as `source: "invite"` participants when the meeting has no stored rows, and matched by the participant filter |
| `meetings.data['participants']` — platform-observed roster | the bot / capture lane | unchanged; **projected read-only** as `source: "platform"` |
| `meetings.data['speaker_events']`, `transcriptions.speaker` | the capture + ingest lanes | untouched — this is the SPEAKER layer and stays where it is |

So the table is additive over the existing stores rather than a second copy of them: a caller that
attaches a roster gets rows, a deployment that never attaches one still answers the same read path
from the JSONB it already has.

## Absence is not an empty roster

Three meetings in the current corpus have no roster at all, and "we never captured one" must not
read as "the roster was empty". The read path answers with `participants_source`:

- `"none"` — no roster was ever captured for this meeting (**absence**);
- `"invite"` / `"platform"` / `"inferred"` / `"mixed"` — a roster WAS captured, and
  `participants: []` under one of these means it was genuinely empty.

The attach route stamps `meetings.data['roster_capture'][<source>] = {count, at}` so a roster that
is captured and empty leaves a trace even though it produces no rows.

## Production rollout

Deploy normally. On boot, `ensure_schema` creates the table and its three indexes; the build is on
an empty table, so the `CREATE UNIQUE INDEX`-on-dirty-data hazard from MIGRATION-0002 does not
apply. `ON DELETE CASCADE` + the `passive_deletes=True` relationship means deleting a planned
meeting drops its roster with it (a planned row can carry a roster before it ever runs).

## Rollback

`DROP TABLE meeting_participants;` and revert the SSOT + mirror change. No other table is touched,
and every pre-existing store (`data['attendees']`, `data['participants']`, `speaker_events`,
`transcriptions.speaker`) is exactly as it was — so a rollback loses only rosters attached through
the new route, and only those.
