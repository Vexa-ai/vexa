# Run the cookbook live — the human validation surfaces

Each surface drives ONLY `vexa_slim.cookbook.*`. If it works for you, the cookbook works.
All commands run from `clients/slim/`. A working key is already saved in `clients/terminal/.env.local`.

## ★ The control panel (one page, click around)
    python -m vexa_slim.web          # → open http://127.0.0.1:8800

Four tabs, all live against your stack:
- **Chat / Onboard** — click *Start onboarding* to run the cold-start interview, or just type. Multi-turn
  (the `session` box). The agent writes entities as you talk.
- **Workspace** — your `kg/entities/*`; click any to read it.
- **Meeting** — paste a real Google Meet URL → *send bot + watch* → live notes + cards stream in.
- **Routines** — schedule one + see the list.

The CLIs below are the same verbs if you prefer a terminal.

---

## 0 · Auth (once)
A key is already provisioned. To make your own fresh user (and a fresh cold-start workspace):

    python -m vexa_slim.play login --email you@example.com --admin-token "$(docker exec vexa-v012-admin-api-1 printenv ADMIN_API_TOKEN)"

This saves `VEXA_API_KEY` into `clients/terminal/.env.local`; every command + the web view pick it up.

## 1 · Onboard (interactive — you type)
    python -m vexa_slim.play onboard

The agent interviews you, does light web research, and writes durable entities
(`kg/entities/person/…`, `kg/entities/company/…`). Blank line / Ctrl-D to finish; it prints what it wrote.

## 2 · Chat (pure, or grounded on a meeting)
    python -m vexa_slim.play chat "what do you know about me?"
    python -m vexa_slim.play chat "summarize the call" --meeting abc-defg-hij

## 3 · Routine (schedule + validate it compiled to a job)
    python -m vexa_slim.play routine daily-graph --cron "0 18 * * *" \
      --prompt "review today's meeting docs and update kg/entities"

## 4 · Live meeting processing (web)
    python -m vexa_slim.web        # http://127.0.0.1:8800

Open it, paste a **real Google Meet URL**, click **send bot + watch** — the bot joins, and cleaned
**notes** + **cards** (people/companies/decisions) stream in as the meeting is processed.
