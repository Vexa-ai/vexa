# proactive-card.v1 — a unit's proactive output to a surface — UNSEALED (stub)

A structured proactive output (not a chat reply) a unit pushes over the **unit output bus** (`ws.v1`
per-unit topic, `mode=card`): the live in-meeting agent's new-person/action-item/decision cards, inbox
triage's proposed actions, triage digests. Each card `action` dispatches a command / `unit.v1`
Invocation (e.g. "Create task") — the uniform **proactive-card → action → send** loop every proactive
surface reuses. Owner = the `identity.v1` subject (per-person).

**Status: UNSEALED** (stub) — sealed in the proactive-surface (Live) MVP. `gate:schema` validates its golden.
