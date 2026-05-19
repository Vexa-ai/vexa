# Release-doc template — canonical

This file defines the **canonical structure** for every human-signing
release document — `scope.md` at plan-solution, the same file evolving
through the cycle into `RELEASE_NOTES.md` at release. Single source of
truth.

A release doc that doesn't follow this template is a finding at the
next-stage audit.

---

## Required sections (in order)

1. **Title + sign attestation** — three-point oath (read multiple times, understand, balance is deliverable).
2. **What you get** — user-visible improvements. Most material first.
3. **Gaps closed** — technical or architectural debt paid down.
4. **Decisions made** — ADRs the release commits to (durable design calls).
5. **Trade-offs acknowledged** — bounded costs the signer accepts.
6. **Deferred** — items not in this release, with target follow-up.
7. **Next-cycle commitments** — packs identified mid-cycle for the next release.
8. **Open questions** — items blocking sign. Empty at sign time.
9. **Sign block** — canonical per `tests3/sign-template.md`.

---

## Required columns per row (every section)

Every row in sections 2-6 MUST carry two attributes alongside the item:

- **`significance`** — HIGH / MEDIUM / LOW
- **`blast_radius`** — one sentence (different meaning per section, defined below)

These two columns force the signer to confront impact + downside on every item, not just the high-level summary.

### Per-section meaning of significance + blast_radius

| Section | `significance` means | `blast_radius` means |
|---|---|---|
| What you get | impact for users | what fails if this feature regresses |
| Gaps closed | how much debt was being carried | scope of the closure activity itself (migration, refactor, docs) |
| Decisions made | how durable / architectural the decision is | what's needed to unwind if the decision is wrong |
| Trade-offs | how much we give up | who/what is affected by the trade |
| Deferred | how important is the deferred item | what gets worse the longer we defer |

### Significance scale (use consistently across sections)

- **HIGH** — directly affects paying customers, OR data integrity, OR architectural shape going forward.
- **MEDIUM** — affects developer experience, OR a small customer cohort, OR contained technical debt.
- **LOW** — internal hygiene, OR no customer-visible impact.

Mixed-band items (HIGH for one audience, LOW for another) are allowed
but must annotate both: e.g. `LOW customer-visible / HIGH internal`.

---

## Anti-patterns (rejected at plan-audit)

- **Significance column missing.** Reject; the signer cannot weigh
  what they're signing for.
- **Blast-radius column saying "unknown" or "n/a".** If the author
  cannot describe the blast, the item is not ready for the doc.
- **Every row is HIGH.** Significance inflation defeats the column.
  If everything is the most important, nothing is. Force a
  distribution — at most 30% HIGH per section is healthy.
- **Marketing tone in "What you get".** This isn't a press release;
  it's a commitment. Plain language, no superlatives, no "comprehensive".
- **CTO-briefing block at the top.** Wrong artefact type — this is a
  commitment doc, not a decision request. The signer is the principal,
  not the audience for a pitch.
- **Length over what can be re-read twice in a tea break.** Under
  ~200 lines is the budget. If you need more, split into companion
  files (the structured machine-read companion lives in `scope.yaml`).

---

## Lifecycle of the file

The same file passes through every stage:

| Stage | What changes |
|---|---|
| `plan-solution` | Author produces the doc. Sign block empty. Open questions present. |
| `plan-audit` | Audit findings reference the doc; no edits unless author bounces back to `plan-solution`. |
| `plan-human` | Human fills `rationale_in_my_own_words`. Human flips attestations. Open questions empty. **Sign happens here.** |
| `develop-code` | "What you get" rows annotated as work completes. Sign block frozen. |
| `develop-human` | No edits to the signed doc. Local-checklist is a separate file. |
| `stage`, `stage-audit`, `stage-human` | Same — signed doc is durable. |
| `release` | Doc renamed / mirrored to `RELEASE_NOTES.md` for publication. Sections 8 (open questions) and 9 (sign block) may be stripped or moved to a private appendix; public-facing sections remain. |

A signed doc is durable. If facts change after sign, write a new doc
(revision, addendum, retraction) — never edit a signed one in place.

---

## Companion file

The machine-read companion (`scope.yaml`) carries the full structured
per-issue blocks (`code_to_change`, `tests_to_add`, `migration_decision`,
etc.). The release doc summarises; the companion enumerates. Tooling
(`stage.py`, `release-validate`, `release-audit`) reads the companion.

When `scope-md-pivot` lands in v0.10.7, the doc becomes primary and the
companion is auto-derived from fenced YAML blocks in the MD itself.
Until then, both files are dual-authored.

---

## Updating this template

Same rules as `sign-template.md`: write the new version in a
`proposed:` block here, open a v0.10.7+ groom-pack `release-doc-template-vN`
describing why and what changes, then mirror once approved. Existing
signed docs stay valid against the version in force at sign time.
