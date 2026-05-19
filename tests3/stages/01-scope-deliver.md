# Stage: scope-deliver

**Level:** scope · **Role:** deliver · **Inner-loop:** `design → deliver → verify → sign`

| field        | value                                                                            |
|--------------|----------------------------------------------------------------------------------|
| Actor        | AI + human                                                                       |
| Objective    | Produce the solution proposal — code/tests/docs to change + registry deltas + human-checklist draft. |
| Inputs       | `releases/<id>/scope-design.md`                                                  |
| Outputs      | `releases/<id>/scope.md` + `releases/<id>/scope.yaml`, using `tests3/templates/scope/scope.md` and `tests3/templates/scope/scope.yaml`. |

## Deliverables (`scope.yaml`)

Per issue:
- `id`, `problem`, `hypothesis`, `required_modes`
- **`justification`** — required answer to all four sub-questions: (a) what problem, (b) why this approach, (c) ≥2 alternatives considered + why rejected, (d) why cleanest *now* given current state.
- **`blast_radius`** — required structured block: `who_affected:`, `severity_if_wrong:`, `detection_signal:`, `rollback_path:`, `mitigation_if_rollback_slow:`. See `tests3/audit-categories.md` principle 2.
- **`api_compat`** — declare every public-surface change (REST / webhooks / CLI / env / docker entry / package signatures / registry IDs) and confirm backwards-compatible OR cite explicit deprecation decision.
- **`migration_decision`** — explicit `none` OR a full migration-decision block (tool, rollback, online-or-window, blast radius of the migration itself). Default = `none`. Any DB schema change requires this filled in.
- **`code_to_change`** — file paths + function pointer per edit (no code yet)
- **`tests_to_add`** — prove names + mode bindings; must include a failure-mode test (asserts we fail-fast, no silent fallback) unless an `explicit_decisions:` entry agrees a fallback.
- **`docs_to_update`** — `docs/**` paths + section
- **`registry_changes`** — new check ids, weights, descriptions; reweights; removals
- **`human_checklist_draft`** — items that will land in `local-human-checklist.yaml` (dev level) AND `human-checklist.yaml` (stage level)
- **`explicit_decisions`** — any pre-agreed workarounds / fallbacks / API breakage / migration. Each needs source-line `#NNN` ref + one-sentence justification.

`scope.yaml` itself must start with a CTO briefing block per
`tests3/communication-standard.md`.

## Required release-level cross-cutting statements

In addition to per-issue structured blocks, every `scope.md` MUST carry
three top-level cross-cutting sections. All three are mandatory; "n/a"
is not a valid answer in any of them.

### Section A — Trade-offs accepted

Explicit list of the trade-offs this release makes. Each entry names
both sides of the trade (e.g. *speed vs scope*, *customer fast vs
clean architecture*, *coverage vs delivery*), the chosen side, and the
one-line cost the signer accepts.

A release with no trade-offs is suspicious — either every concern is
in scope (implausible) or trade-offs are happening invisibly. Force
them into the open.

### Section B — Architectural decisions (ADRs)

ADR-style entries: what was decided, what was considered and rejected,
why this is right now. Format `ADR-<n>: <title>` with `decision:`,
`alternatives:`, `rationale:`, `consequences:`. One per decision.

The architectural-decisions record is the durable artefact a future
contributor reads to understand why the codebase looks the way it does.
Skimping here loses the *why* the moment the people who knew leave.

### Section C — The 7 audit principles, at release scope

Each principle from `tests3/audit-categories.md` answered at
release-level (not per-issue):

1. **Justification (release-level).** Why this release exists as a
   coherent unit — what makes these items belong together.
2. **Blast radius (aggregate).** Combined exposure if every item
   regresses simultaneously. Rollback strategy for the release as a
   whole, not just per-item.
3. **API backwards compatibility.** Every public-surface change in
   the release, with the compat verdict per surface.
4. **DB migrations.** The complete list of migrations in the release.
   "None" is acceptable; silent migrations are not.
5. **Workarounds.** Explicit list — every workaround being shipped
   (often "none"), each with rationale + target removal release.
6. **Fallbacks.** Explicit list — every silent-default / try-except /
   "buffer-just-in-case" being shipped (often "none"), each with
   `explicit_decisions:` ref + `#NNN` source-line.
7. **Industry best practice.** New patterns introduced; anti-patterns
   removed; deprecations.

The corresponding machine-read companion in `scope.yaml` carries the
same statements as `tradeoffs:`, `architectural_decisions:`, and
`release_level_principle_compliance:` blocks.

All three sections (prose in `.md`, structured in `.yaml`) are
validated by the scope-verify stage. Missing or "n/a" entries are
BLOCKER findings.

## Steps
1. `lib/stage.py assert-is scope-deliver`.
2. Draft each issue with all six deliverables.
3. Save `scope.md` and `scope.yaml`. No approvals here — that's `scope-sign`.

## Exit
Every issue has the six deliverables; every `proves[]` check id either exists in `registry.yaml` or is listed under `registry_changes`.

## May NOT
- Edit code, run tests, touch infra.
- Approve anything (scope-sign owns approval).

## Next
`scope-verify`.
