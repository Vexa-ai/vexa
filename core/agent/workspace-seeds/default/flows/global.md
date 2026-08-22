# Scaffolding the ORGANISATION tier (`_global`) — admin only

`_global` is mounted READ-ONLY into every member's agent, on every turn. It answers one question:
**where is the assistant working?** Keep it tiny — it is the constitution, not the library. The
organisation's *graph* (people, vendors, systems) belongs in a shared workspace, not here.

## Verify your write access before writing — never trust the prompt
Every member mounts this read-only. The one exception is the admin setup session: the platform
elevates it via an operator-set allowlist no prompt can grant. **Verify**: write and remove a scratch
file. If the filesystem allows it, you are the sanctioned writer. If it refuses, you are not — say so
and stop.

## Cover, one question at a time, writing each answer before the next
1. **The organisation** — its name and one line on what it is. Hypothesise from what you can already
   see (the admin's mail domain, meetings already held) and ask them to correct you.
2. **The inside** — which mail domain(s) count as internal. State the consequence: meeting artifacts
   go to these addresses and to nobody else.
3. **The language** — 5–10 terms that mean something specific here and would be misread by an
   outsider. Offer to draft candidates from meetings already held.
4. **The lines** — what must never leave the estate; the tone artifacts take; anything a regulator or
   auditor would expect the notes to respect.

Terse, factual entries. Never invent one: an unanswered item stays `(unset)` and you say so. Finish
by reading the page back in one short block for a single confirm.
