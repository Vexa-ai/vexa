---
name: scheduling
description: Set up recurring or scheduled work — anything the user wants run on a schedule, repeatedly, or at a future time ("every morning", "each weekday at 9am", "daily digest", "weekly report", "remind me", "run this on a cron"). Teaches how to author a durable Vexa Scheduler routine that survives restarts. Use this instead of any OS cron job, system timer, or session-only reminder.
---

# scheduling — durable recurring work

When the user asks for ANYTHING that should run on a schedule or repeat over time
(a daily digest, a weekly report, a periodic check, a future reminder), you create
a **routine file** in this workspace. The agent-api reconciler compiles it into a
**durable Vexa Scheduler job** that survives restarts and process death — it is the
only correct mechanism for scheduled work here.

**NEVER** do any of these for scheduled work:

- create an OS cron job (`crontab`, `/etc/cron.*`)
- create a systemd timer or any other system timer
- start a shell background loop / `sleep` loop
- leave a "session-only" reminder (your turn ends and it is gone)

All of those die on restart or when your turn ends. Only a routine file is durable.

## How to create a routine

Write one file at **`routines/<name>.md`** (`<name>` is a short slug; the filename
stem becomes the routine name). It is a normal governed workspace file — just write
it; you do not run git.

The file is **YAML frontmatter** plus an **optional markdown body**:

| field     | required        | meaning                                                        |
|-----------|-----------------|----------------------------------------------------------------|
| `enabled` | no (default `true`) | `false` cancels the job but keeps the file for later re-enable |
| `cron`    | yes (when enabled)  | a standard **5-field** cron expression (min hour dom mon dow)  |
| `prompt`  | yes (when enabled)  | the instruction sent to the agent on each run (non-empty)      |

The optional markdown body below the second `---` is appended to `prompt` as extra
context for each run.

### cron format

Five fields, space-separated: `minute hour day-of-month month day-of-week`.
Day-of-week is `0`–`7` (0 and 7 are both Sunday); `mon`/`tue`/… and `jan`/`feb`/…
names are accepted, as are ranges (`mon-fri`), lists (`1,15`), and steps (`*/15`).

Examples:
- `0 8 * * *` — every day at 08:00
- `30 9 * * mon-fri` — 09:30 on weekdays
- `0 17 * * fri` — 17:00 every Friday
- `0 * * * *` — top of every hour

### Worked example

For "every weekday at 9am, give me a digest of yesterday's meetings", create
`routines/morning-digest.md`:

```markdown
---
enabled: true
cron: "30 9 * * mon-fri"
prompt: "Compile a short digest of yesterday's meetings and open tasks from kg/entities/, and summarize what needs attention today."
---

Keep it under ~10 bullet points. Group by person/company. Link entities with [[wikilinks]].
```

That's it. The reconciler (running in agent-api, scanning every workspace on an
interval) picks the file up, validates the cron + prompt, and schedules a durable
Vexa Scheduler job. Editing the file updates the job; setting `enabled: false` (or
deleting the file) cancels it.

## Validation notes

- An **enabled** routine MUST have a valid 5-field `cron` and a non-empty `prompt`,
  or the reconciler logs a warning and skips it (no job is created). Double-check the
  cron has exactly five fields.
- A **disabled** routine (`enabled: false`) needs no cron/prompt — it just cancels
  any existing job for that name.
- Confirm to the user what you scheduled (name + cron + what it does).
