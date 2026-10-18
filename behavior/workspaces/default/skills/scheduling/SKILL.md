---
name: scheduling
description: Set up recurring or scheduled work — anything the user wants run on a schedule, repeatedly, or at a future time ("every morning", "each weekday at 9am", "daily digest", "weekly report", "remind me", "run this on a cron"). Teaches how to make a routine — the person's own flow, a schedule plus one agent turn — with the flows tools. Use this instead of any OS cron job, system timer, file, or session-only reminder.
---

# scheduling — a routine is the person's own flow

When the person asks for ANYTHING that should run on a schedule or repeat (a daily digest, a weekly
report, a periodic check, a reminder), you make a **routine**: a flow of theirs with a schedule
trigger and the `agent_run` step. Flows keeps the clock, in its database, so it survives restarts.
It is the only mechanism for scheduled work here.

**NEVER** do any of these for scheduled work:

- create an OS cron job (`crontab`, `/etc/cron.*`), a systemd timer or any other system timer
- start a shell background loop or `sleep` loop
- write a file under `routines/` — files there do nothing
- leave a "session-only" reminder (your turn ends and it is gone)

## How to make a routine

Call **`flows_submit`**:

```json
{
  "name": "morning-digest",
  "trigger": { "kind": "schedule", "cron": "30 9 * * 1-5" },
  "steps": ["agent_run"],
  "params": { "instruction": "Compile a short digest of yesterday's meetings and open tasks, and say what needs attention today. Keep it under ten bullets.",
              "thread": "<this chat's thread id>" }
}
```

- `name` — a short slug. Submitting the same name again writes a new version of that routine.
- `cron` — five fields (minute, hour, day of month, month, day of week), read in **UTC**. Convert
  the person's local time to UTC, and tell them the UTC time you used.
- `params.instruction` — required: what the agent does on each run. Write it so it stands alone;
  the run has nobody to ask.
- `params.thread` — this chat's thread id (it is stated at the top of your turn). The routine runs
  IN THIS CHAT: every run continues this conversation. Leave it out only when there is no chat.
- `params.notify` — only when the person has chosen a connection for alerts and it works. Without
  it, results stay in the chat.
- Do not pass `scope`. It is taken from the person's own credential (`user:<them>`).

Cron examples: `0 8 * * *` (08:00 daily) · `30 9 * * 1-5` (09:30 weekdays) · `0 17 * * 5`
(17:00 Fridays) · `0 * * * *` (hourly).

## What the person gets

- **This chat.** Every run is a turn in the chat the routine was made in; the chat list marks it
  as a routine and it shows Pause and Retire. Each run sees the earlier runs, and the person can
  reply; the next run sees the reply. (A routine made with no chat gets a chat of its own.)
- **Steering:** `flow_lifecycle` with `pause`, `resume` or `retire` on the routine's newest
  version; `flows_list` shows their flows, `flow_runs` the runs.
- **Moving a routine here** ("claim it here", "run it in this chat"): `routine_claim` with this
  chat's id. One operation — never re-submit a routine to move it.
- **A chat as a standing concern** (e.g. production monitoring): it may own several routines;
  `routine_add_trigger` gives it another timer. Each wake is a turn here with this chat's history.
  On a wake where nothing changed, answer one line starting `Checked — ` and nothing else.

## Say only what you checked

After any change to a routine, state only what the tool's answer (or `flows_list`) says: the active
version, the chat it runs in, the next run. Never say a version "should have stopped" — check it.

## What a run may do

Runs are **unattended** (the `autonomous` regime): nobody is in the loop. They can use only the
connections and actions company policy allows for unattended runs (`connection_actions_unattended` in
`_global/POLICIES.md`). Anything else is refused. When a
routine is refused for the same reason three runs in a row, it pauses, and the reason is posted
into its chat. Say this when the instruction needs a connection.

A worker with nobody in the loop cannot make or steer routines: do it only in a turn with the
person present.

## Refusals you may get

| Refusal | Meaning | What to do |
|---|---|---|
| `quota_min_interval` | the cron fires more often than the person's minimum gap (`VEXA_FLOWS_USER_MIN_INTERVAL_S`, 900 s by default) | offer a slower cron |
| `bad_cron`, `instruction_required` | not five valid fields; no instruction | fix and resubmit |
| `step_not_allowed` | a step other than those the deployment allows people (`agent_run` by default) | use `agent_run` |
| `quota_active_flows` | too many active flows | ask which one to retire |

Confirm in one line: the name, when it runs (local time and the UTC cron), and that it runs in this chat.
