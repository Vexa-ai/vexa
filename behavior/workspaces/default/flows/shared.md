# Scaffolding a SHARED workspace (read when this workspace is being set up for a group)

A shared workspace is a **folder several people work out of** — a meeting series, a vendor, a
project, a team's knowledge. It is not a person's memory, so do not run personal discovery here:
nobody's LinkedIn, no `self: true` person entity, no "getting to know you".

## Ask, one question at a time — and write as you learn
1. **What is this workspace called, and what lives in it?** One line is enough; infer a slug from it.
2. **What does winning look like here, and by when?** One sentence, plus the date if one exists.
   This is the workspace's objective — record it in `README.md` under `## Objective`; every meeting
   filed here has its notes ranked against it (what moved it, what blocks it). "No objective — this
   is a reference folder" is a valid answer; write that instead.
3. **Who belongs?** Named emails, or "my organisation" (anyone on the org's mail domain who attends
   its meetings). Say the consequence plainly: members receive its meeting artifacts; nobody else does.

## Then write these three, and commit each
- **`CLAUDE.md`** — the map any agent mounting this workspace reads first: what this folder is, what
  belongs in it, what does not, and any convention specific to it. Keep the entity contract; add the
  local rules.
- **`PURPOSE`** — ONE line: what this workspace is for. When several workspaces are mounted at once,
  this is what routes a write to the right one, so make it discriminating, not decorative.
- **`README.md`** — its face page: purpose, what it pays attention to, who is in, where things
  stand — each as a link to the page or entity that holds it, never a bare name.

## Drive to the accept
You are the gate: keep each reply ending with the next missing piece until `CLAUDE.md`, `PURPOSE`
and `README.md` exist and the membership is settled — then accept by writing `.scaffolded`
(content: today's date) and committing it.

## Finish by telling them how meetings arrive
Put `#group:<slug>` in a calendar invite's description and that meeting — and every later occurrence
of the series — is filed here instead of a personal workspace.
