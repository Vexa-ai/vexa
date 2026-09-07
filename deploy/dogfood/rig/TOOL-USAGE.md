# Which rig MCP tools real sessions actually call

Measured 2026-09-01 on `bbb`, to size the pilot's port. The rig serves **53 tools**; the shipped
`v012-mcp` is a different 14-tool codebase. The question this answers: which subset do chat
workers actually need.

## The primary source cannot answer this — two defects, both structural

The brief named `/tmp/storm-logs/control-mcp.log` as the primary source. It cannot be one, and the
reason is worth recording because it will keep being true until someone changes it:

1. **The rig logs no tool calls at all.** `vexa_control_mcp.py` contains exactly three `print`
   statements: the startup banner and two auth lines. Grepping the log for `tool|call|invoke`
   returns **0**. The 344 lines are 195 auth records plus framework chatter. Tool-call telemetry
   does not exist in that log by construction — not "was rotated away", not "was not enabled".
2. **`rig.sh` opens it with `tee`, not `tee -a`** (`rig.sh:46`), so **every rig restart truncates
   it**. Its history is bounded by the last restart — at measurement time, 19:33 today. A 48h
   window is not recoverable from it, and no rotated copies exist (`/tmp/storm-logs/` holds one
   `control-mcp.log` and nothing else).

A third point matters for the caller-class question specifically: **only the delegated branch
prints.** A `?c=` setup code and a hand-minted bearer both resolve `sub` *before* the delegated
branch is reached (`vexa_control_mcp.py` ~1322–1393), so `if tok and not sub` is false and nothing
is written. The log is therefore 100% `[delegated]` **because delegated is the only class it can
show** — not because all traffic is delegated. Reading a caller-class mix off that log would be
a measurement artifact.

**What was measured instead.** Worker containers symlink `/root/.claude/projects` →
`/workspaces/.system/<uid>/.claude/projects`, which lands on the shared volume
`vexa-dogfood_agent-workspaces`. Those are Claude Code session transcripts and they record every
tool call by name. **45 sessions** were swept; every session containing a vexa call falls inside
the last 48h (2026-08-31 17:58 → 09-01 22:33). `docker logs` on the worker containers is empty for
all of them, so the transcripts are the only worker-side record.

## The table

`calls` = tool_use invocations. `sessions` = distinct transcripts it appears in. `searched` = times
the tool was named in a `ToolSearch` query — the worker harness defers tools, so a search is
recorded intent even where no call followed.

| # | tool | calls | sessions | searched | caller class |
|---|---|---:|---:|---:|---|
| 1 | `whats_waiting` | 21 | 17 | 19 | delegated worker |
| 2 | `meeting_transcript` | 6 | 4 | 4 | delegated worker |
| 3 | `meetings_list` | 5 | 4 | 5 | delegated worker |
| 4 | `workspace_tree` | 4 | 1 | 2 | delegated worker |
| 5 | `workspaces` | 3 | 1 | 4 | delegated worker |
| 6 | `bot_send` | 2 | 2 | 1 | delegated worker |
| 7 | `bot_stop` | 2 | 1 | 1 | delegated worker |
| 8 | `meeting_info` | 2 | 2 | 1 | delegated worker |
| 9 | `company_context` | 2 | 2 | 3 | delegated worker |
| 10 | `workspace_new` | 1 | 1 | 1 | delegated worker |
| 11 | `workspace_read` | 1 | 1 | 1 | delegated worker |
| 12 | `report_friction` | 1 | 1 | — | delegated worker |
| 13 | `flows_list` | 1 | 1 | 4 | delegated worker |
| 14 | `propose` | 1 | 1 | 2 | delegated worker |
| 15 | `flow_lifecycle` | 0 | — | 3 | delegated worker (intent only) |
| 16 | `flows_submit` | 0 | — | 2 | delegated worker (intent only) |
| 17 | `start_onboarding` | 0 | — | 2 | delegated worker (intent only) |
| 18 | `validate` | 0 | — | 2 | delegated worker (intent only) |
| 19 | `vexa_overview` | 0 | — | 1 | delegated worker (intent only) |
| 20 | `workspace_purpose` | 0 | — | 1 | delegated worker (intent only) |
| 21 | `mark_scaffolded` | 0 | — | 1 | delegated worker (intent only) |

**52 vexa calls total. 14 tools called, 21 touched (called ∪ searched), 32 of 53 never touched.**

Non-vexa tools in the same sessions, for proportion: `Read` 84, `Write` 63, `Bash` 61,
`ToolSearch` 60, `WebSearch` 44, `Edit` 35, `WebFetch` 26, `Glob` 10, `Grep` 3, `Agent` 2, plus
`mcp__claude_ai_Google_Calendar__list_events` 4 and `mcp__claude_ai_Gmail__search_threads` 1.

### Never touched by any worker (32)

`auth_claim`, `auth_link`, `bot_config`, `bot_say`, `bot_schedule`, `bots_running`,
`captions_to_segments`, `confirm_login`, `deeplink`, `fact_emit`, `friction_so_far`, `mail_inbox`,
`mail_read`, `meeting_delete`, `meeting_participants`, `meeting_seed`, `meeting_update`,
`reaction_signal`, `reactions_list`, `recordings_list`, `settings`, `transcript_search`,
`user_ensure`, `vexa_search_docs`, `workspace_init`, `workspace_invite`, `workspace_members`,
`workspace_pull`, `workspace_regime`, `workspace_remove`, `workspace_write`,
`zoom_transcript_to_segments`

## The reading

**The port is roughly 14 tools, not 53, and one tool carries the loop.** `whats_waiting` is 40% of
all vexa traffic and appears in 17 of 45 sessions — it is the session entry point the server's own
protocol demands, and a port that shipped nothing else would still function as a degraded loop.
Around it sit three small clusters that account for nearly all remaining traffic: **meetings**
(`meetings_list`, `meeting_transcript`, `meeting_info`), **bots** (`bot_send`, `bot_stop`), and
**workspace reads** (`workspaces`, `workspace_tree`, `workspace_read`, `workspace_new`). Add
`company_context`, `propose`, `flows_list` and `report_friction` and you have every tool a real
worker invoked. The safe port is the 21-tool union — the extra seven (`flow_lifecycle`,
`flows_submit`, `start_onboarding`, `validate`, `vexa_overview`, `workspace_purpose`,
`mark_scaffolded`) were reached for by name and are cheap insurance against a worker that searches
for a tool the port does not carry.

**The 32 untouched tools are, in the main, the person-agent's surface rather than the worker's** —
and the split is coherent, not accidental. The registration and identity plumbing (`auth_claim`,
`auth_link`, `confirm_login`, `deeplink`, `user_ensure`, `workspace_init`) exists to serve the
`?c=` path by which a *person* hands their own agent an authenticated Vexa; a delegated worker is
already authenticated and never walks it. `settings` sits in the same class — a person's
preferences, edited by the person's own agent, which is why extending its vocabulary today touched
nothing a worker does. Operator-grade bot control (`bot_config`, `bot_say`, `bot_schedule`,
`bots_running`), the media and transcript utilities (`captions_to_segments`,
`zoom_transcript_to_segments`, `transcript_search`, `recordings_list`, `meeting_participants`,
`meeting_update`, `meeting_delete`, `meeting_seed`), the mailbox pair, and the rig's own
instrumentation (`fact_emit`, `friction_so_far`, `reaction_signal`, `reactions_list`) are likewise
founder-and-rig surface. None of it needs porting; the founder keeps using the rig.

**One caveat that would change the answer, and it is the important one.** The workspace tools look
unused, but workers called `Write` 63 times and `Read` 84 — they edit workspace files *directly*,
because the rig mounts `/workspaces/<uid>` into every worker container. `workspace_write` and
`workspace_pull` are unused **as an artifact of that mount, not as a statement of need**. A ported
chat worker that does not get the same volume has no other route to a workspace and will need the
write side of that cluster on day one. Do not read their zeroes as absence of demand.

**Confidence.** 52 vexa calls across 45 sessions is a small sample, and these are rehearsal-rig
sessions on the founder's own host, not pilot chat workers — the mix is development-shaped
(heavy `Bash`/`Write`/`WebSearch`). Two demand signals sit just under the surface and are worth
watching rather than acting on: three keyword searches for shared-workspace provisioning
("create shared workspace invite team member" ×2, "create group workspace provision") resolved to
no call, which points at `workspace_invite`/`workspace_members` as the next tools to be wanted.
The founder's own agent could not be measured at all — `/home/dima/.claude/projects` on `bbb` is
empty, so his sessions run off-host, and per the logging defect above the rig retains no record of
them. The founder-only claims here rest on what workers never touched plus the shape of the tools,
not on observed founder traffic.

## If we want this measured properly

One line in the auth path that logs the resolved tool name and caller class for every call — and
`tee -a` in `rig.sh:46` so a restart stops erasing the evidence — would replace this entire
reconstruction with a `sort | uniq -c`.
