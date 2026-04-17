---
name: 0-groom
description: "Invoke BEFORE stage 1 (plan) of a Vexa release cycle. Triages the incoming issue sources — open GitHub issues at Vexa-ai/vexa AND human bug reports posted in the Discord support channel — then groups related items into issue packs ready for `1-plan`. Use when the user says 'groom the backlog', 'triage issues', 'what should we ship next', 'check the support channel', 'pack these issues', 'plan the next release' (first pass), or any pre-planning question about incoming work."
---

## Stage 0 of 9 — groom (inputs to stage 1)

Stage 1 (plan) is the contract for a release; stage 0 gathers the raw material. Two input sources, one combined shortlist.

## Sources

### A. GitHub issues

```bash
# Open issues, most-recent first
gh issue list --repo Vexa-ai/vexa --state open --limit 30 \
    --json number,title,labels,milestone,assignees,updatedAt,body \
    | jq -r '.[] | "#\(.number)\t\(.updatedAt[:10])\t\(.title)"'

# Stale issues (> 30 days since last touch) — candidates to close or refresh
gh issue list --repo Vexa-ai/vexa --state open --limit 50 \
    --json number,title,updatedAt \
    | jq -r '.[] | select(.updatedAt < (now - 30*86400 | strftime("%Y-%m-%d"))) | "stale #\(.number)\t\(.title)"'

# Specific area/label (e.g. webhooks)
gh issue list --repo Vexa-ai/vexa --label webhook --state open
```

For each issue you intend to carry into the next release:

- Skim the body + comments for duplicates of other issues. Merge (close one → `Relates to #N`) when clear.
- If context is stale, post a comment asking for repro or fresh details.
- Assign to a milestone named after the likely release ID (`260417-webhooks-dbpool`).
- Apply labels: `bug`, `enhancement`, `infra`, `security`, `docs`. Add one component label (`webhooks`, `bot`, `dashboard`, `helm`, ...).

### B. Discord support channel

Server: **Vexa Community** (`guild_id=1337394383888060436`). The bug reports live in `#support` and `#general`. The bot (see `/home/dima/dev/0_old/skills/discord/README.md`) has read permissions.

```bash
# Via the bot — last N days of messages in support channels
# (Use the existing discord bot script; do NOT paste tokens here.)
node /home/dima/dev/0_old/skills/discord/scripts/fetch-support.mjs --since 7d > /tmp/discord-support.jsonl

# Or manually browse https://discord.gg/Ga9duGkVz9 → #support
```

For each user bug report worth acting on:

1. **Reproduce or request repro**. A Discord message is not a filed bug until we can reproduce or the user provides concrete repro steps + environment.
2. **File a GH issue** (don't let reports die in Discord):
   ```bash
   gh issue create --repo Vexa-ai/vexa \
       --title "<short user-visible symptom>" \
       --body "$(cat <<EOF
   Source: Discord #support message from @<user> on YYYY-MM-DD
   Link: https://discord.com/channels/1337394383888060436/<channel>/<message>

   ## Report
   <quote the relevant portion>

   ## Environment
   - Deployment: <lite|compose|helm>
   - Version: <commit or tag>

   ## Repro
   1. ...
   EOF
   )"
   ```
3. **Reply in Discord** with the issue link so the user can follow along.

## Produce a shortlist

Group related issues into **packs** of 2-5 that form a coherent release:

```
Pack: webhooks (for 260417-webhooks-dbpool)
  #191  Webhook config never reaches meeting.data
  #198  Status-change webhooks not firing on short meetings
  #207  DB pool exhaustion under webhook retry load
  #211  Transcripts lost after Redis consumer-group eviction

Pack: helm deploy
  #204  Helm chart missing DB idle timeout
  #219  NetworkPolicy blocks meeting-api → external webhooks
```

Packs become the rows in `scope.issues[]` at stage 1. A pack is a release-size unit of work — don't try to ship more than 5 issues at once.

## Hand-off to stage 1

When the shortlist is ready:

```bash
make release-plan ID=<release-id>
$EDITOR tests3/releases/<id>/scope.yaml
```

Use the pack contents to fill in `issues:` entries. Each pack issue becomes one scope issue with `problem`, `hypothesis`, and `proves:` bindings.

## Close the loop after ship

After stage 8 (ship):

```bash
# For each scope issue that shipped:
gh issue close <N> --comment "Shipped in release <tag> (PR #<N>). See tests3/reports/release-<tag>.md."

# In Discord, post a release summary + thank reporters:
# #announcements channel, short markdown summary of fixes with user @mentions.
```

## Ground rules

- **No hidden work.** A Discord report that isn't filed as a GH issue cannot go into a scope. Scope only references GH issue numbers (via `ref:`).
- **Stale means closed.** Issues untouched for 30+ days without recent repro context get a "pinging for repro" comment; another 30 days without response → close as `won't-fix`.
- **Don't mix concerns.** A pack should be one coherent theme. Don't pack "webhooks fix" with "dashboard redesign".

## Next

With packs decided: → stage 1 (`1-plan`). Copy each pack into one `scope.issues[]` entry. The scope file is the contract that every downstream stage reads.
