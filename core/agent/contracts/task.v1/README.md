# task.v1 — a task (a unit of work with state) — UNSEALED (stub)

A to-do with state (`open|doing|done|blocked`), created from meetings, email, routines, or chat. It
lives in the per-person workspace as a `kg/entities/task/<slug>.md` (a `workspace.v1` entity) **and**
has this wire shape for the event bus — a task transition is itself an `event` trigger source. Owner =
the `identity.v1` subject (per-person).

**Status: UNSEALED** (stub) — sealed in the tasks MVP. `gate:schema` validates its golden.
