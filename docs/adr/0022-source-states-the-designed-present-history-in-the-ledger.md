# ADR 0022 — Source states the designed present; history lives in the learnings ledger

**Status:** proposed · 2026-06-22 · rides `lane:contract` · composes ADR-0018 (learnings ledger) / ADR-0021 (planning embeds the rules) · extends P21 + ADR-0013/0016 (report from evidence, not evaluations)

## Context

Across the dashboard-walk milestone, fixes accreted **incident history into the source**: code comments
carried "before this it was X", "the carve had…", "`(Learning #N)`", "was 404 / 405'd", "this was the Y
bug". That is rot — it freezes transient context into the source, decays into a lie as the code evolves,
and duplicates what the learnings ledger already records. The same history-as-design confusion let a
*client's legacy shape* get pushed upstream into a core publisher (a comment justified the leak by narrating
the bug). The project already has distinct documentation homes — `docs/ARCHITECTURE.md` (constitution /
principles), `docs/adr/` (decisions), `docs/LEARNINGS.md` (the incident ledger, ADR-0018),
`docs/RELEASE-PLAN.md` (the plan), `AGENTS.md` (operating contract) — but no rule for **what belongs where**,
so incident narrative bled into code comments.

## Decision

- **Source states the designed present.** Code comments, file headers, and docs describe what the thing
  **is** and **why**, in terms of *today's* contracts — never what broke, what was stale, what the fix was,
  or which Learning/ADR number prompted it. Write each comment as if the code had always been this way. (This
  is the *output* side of P21 / ADR-0013: report from the designed state, not from the incident.)
- **History lives in the ledger.** The `surprise → root-cause → fix` narrative lives **ONLY** in
  `docs/LEARNINGS.md` (ADR-0018). A `Learning #N` pointer, a "(was X)", or a "carve gap / 404'd" note in
  source is the ledger leaking into the source, and is cleaned on sight.
- **Each documentation home has one job, no overlap:** **ledger** = the incident · **ADR + architecture** =
  the durable principle · **plan** = the route · **source** = only the design.
- **Close the loop: incident → principle → plan.** A learning is not done when it lands in the ledger; the
  durable rule is conceptualized as a principle, promoted to the architecture (an ADR + `AGENTS.md`), and
  **minted inline into the plan** (ADR-0021), so the next plan cannot repeat the class of failure. Promoting a
  learning into a code comment is the anti-pattern (incident → comment); the path is incident → principle → plan.
- **Enforcement.** Review (and any doc-hygiene gate) rejects history markers — `Learning #`, `(was …)`,
  "the carve had", "404'd"/"405'd", "before this", "the … bug", "the fix" — in `*.py` / `*.ts` / `*.tsx`
  comments anywhere outside `docs/LEARNINGS.md`.

## Consequences

- `AGENTS.md` carries the rule inline ("Source states the designed present, not its history" + the
  "Closing the loop — a learning becomes a principle, minted into the plan" section), so it binds planning.
- This milestone's source was audited and cleaned (meeting-api app/lifecycle/recordings/bot_spawn/collector,
  gateway, admin-api, the bot `record-chunker` + `recording-assembler`, the dashboard WS files, `judge.py`,
  the durable test); `grep "Learning #"` over `core/` + `clients/` source is empty — the markers survive only
  in `docs/LEARNINGS.md`, where they belong.
- Pairs with a sibling decision on **contract ownership / dependency direction** — the core owns its
  contracts and emits the clean canonical shape; adapters and legacy/vendored clients absorb the impedance on
  their side; a consumer's legacy name or shape is never pushed upstream into the core — recorded separately
  (proposed ADR-0023), already embedded in `AGENTS.md`'s hard rules.
