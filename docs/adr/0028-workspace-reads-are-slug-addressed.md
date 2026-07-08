# ADR 0028 — Workspace reads are slug-addressed; the active set is the SSOT of visibility

**Status:** accepted · 2026-07-08 · applies **P4 (SSOT)**, **P8 (SoC)** to the workspace read path ·
sibling of ADR-0027's "one fact, one carrier" — the same disease on the workspace plane

## Context

The flat, equal-rank workspace model (`d5ac42fe`) removed the "baseline" rank: `active_set` is an
ordered list of equal workspaces, `m.primary` means only "the dynamic HOME — the turn's cwd (first
active)", and `_seed_slot_slug` is documented as *"path resolution only — a storage detail, not a
rank"*. But the READ path still carries the old world: a no-slug read (`GET /api/workspace/tree`,
`/file`, `/git`, chat continuity conventions) resolves to **`<root>/<subject>` — the seed-slot
storage directory** — whatever tree physically occupies it, mounted or not.

That gives the fact *"which workspace content is visible"* two competing carriers:

1. the **active set** (authoritative, slug-addressed, real mount paths), and
2. the **implicit no-slug default** (a storage address exported as an identity).

Two user-visible defects on the eyeball (2026-07-08, user 28) traced to exactly this split:

- **"Chats list but don't load."** With Personal deactivated, the worker cwd followed the active
  set (a shared workspace); chat continuity was rooted at the **cwd** while the history reader
  searched only `<root>/<subject>` → `{turns: []}`, plus private transcripts stranded on a shared
  volume. (Fixed by anchoring continuity to `_system` + a multi-root reader, `a41a5f95`/`c5003fcd`
  — the write-side symptom of this ADR.)
- **"Workspace 3 is checked but I see Personal."** The finder gated on the active set
  (`m.primary` present ⇒ "show the home tree") but fetched the main tree with **no slug** — the
  seed slot — showing a *deactivated* Personal's files under a checked workspace "3", whose tree
  lives at `.attached/28/3-c75688c6` and was never fetched (the per-slug loop skipped the primary).

Every new consumer was inheriting the flaw at accretion speed: the docLinks resolver, the manage
panel, the admin panel all consume this API family.

## Decision

**Every workspace read is addressed by slug, and the ACTIVE SET is the single source of truth for
what is visible.** Concretely:

1. **No consumer may issue a no-slug read to mean "the user's workspace."** The no-slug form is
   DEPRECATED — transitionally it still resolves to the seed-slot dir server-side, but clients
   treat it as *the legacy last-resort only* (docLinks searches it strictly after every active
   mount; nothing renders from it as a primary source).
2. **The finder renders exclusively from `readActiveSet()`**: the main section is the HOME mount
   (first active) fetched **by its slug**; every other active mount is its own slug-fetched
   section; an empty active set renders empty. A workspace's files appear iff it is active.
3. **"Own README" / "home" mean the FIRST ACTIVE mount**, never the seed-slot dir
   (`firstView`'s own-readme arm pins `active[0]`'s README by slug).
4. **`m.primary`, `<root>/<subject>` residency, and "Personal" are three different things** and
   may never be used interchangeably. `primary` = the turn's cwd; seed-slot residency = a store
   implementation detail; Personal = one equal-rank workspace.
5. **Write-side twin (already landed):** chat continuity anchors to the `_system` mount, never
   the cwd (see Context).

## Consequences

- Switching workspaces switches ALL content coherently — files, doc tabs, link resolution, README
  landing — because they share one carrier (the active set).
- The no-slug server default becomes removable: once the remaining legacy sites (GitSection's
  source-control view, older readWorkspaceFile call sites) pass slugs, the endpoint form can 410.
- **Deferred, unblocked by this ADR:** physically relocating the seed tree out of
  `<root>/<subject>` into a normal store slot (full de-specialization — kills the seed-slot
  special case at the root, enables share/archive/delete of the seed workspace). Post-ADR, that
  relocation is invisible to every conforming consumer.

## Known remaining no-slug sites (tracked, conforming-transitional)

- `GitSection` (terminal source-control panel) — shows the seed slot's git state.
- Legacy `doc:README.md` tab ids restored from saved layouts.
- Server no-slug endpoint defaults themselves (kept for the transition, deprecated).
