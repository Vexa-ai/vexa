# ADR 0031 — The witness script is generated from the batch; every PR's value is accounted for

**Status:** accepted · 2026-07-14 · sharpens **ADR-0029** (the release witness) and the ship bar's
"the release generates a witness script from the batch" · enforces **D0** (verified truth about
*value*), **line 7** (witnessed value) and **line 8** (every change proven)

## Context

ADR-0029 built the witness gate: no promote without a signed `releases/<v>/witness.json`. But the
receipt's `values_walked` was a free-form list a human hand-wrote — so the witness was only as
complete as the human's memory. In practice that collapsed to "witness the Meet transcript," which
**misses the actual delivered values**: for v0.12.4 the real user-visible values were *Teams bot
stays joined* (#599), *Jitsi lobby admission* (#597), *recording durability* (#611), and
*config fail-closed boot* (#605) — none of which is "a Meet transcript." A witness that doesn't
enumerate the batch silently skips values. That violates D0: the scarce thing we must produce is
*truth about each value*, not a vibe that "it works."

## Decision

**The witness script is generated from the batch, and every PR is an accounted-for entry.**

`scripts/release-witness-script.mjs` enumerates every PR merged since the previous release tag,
and for each writes one `values[]` entry:

- classifies it by the files it touched — **user-visible** (+ platform: ms-teams / jitsi /
  google-meet / zoom) · **backend** · **ci-governance** · **docs**;
- auto-names its **machine evidence** (the test files, seals, gates, CI legs it shipped);
- emits a stub the human resolves.

The human then **resolves every entry**: a user-visible value is **walked live** (`witnessed:true`
+ `observation` + `pass`); a backend/ci value is **by-proxy** with its named evidence. Classification
is best-effort — an over-marked entry is downgraded to by-proxy *with its evidence*, or walked;
either way it is a **conscious per-value decision**. `scripts/release-witness-gate.mjs` (ADR-0029,
now updated) **fails promote until every entry is resolved** — the batch is fully accounted for.

## Consequences

- **No value is silently skipped.** The receipt has one line per PR whether or not a human
  remembers it; the gate refuses an unresolved line. Guarantee lines 7 + 8 are enforced per-value,
  not per-release-vibe.
- **The witness is right-sized.** The human walks only the user-visible values (grouped by
  platform); backend/ci values carry their named proof — the "witnessed by proxy, named as such"
  the constitution always intended, now mechanical.
- **Best-effort classification, honest about it.** A file-path heuristic is coarser than reading
  each PR; it errs toward *over*-marking user-visible (safe: over-ask, never skip). The human
  prunes. A future `--deep` mode could use per-PR analysis for richer entries.
- **Supersedes** the hand-written `values_walked` and `release-witness-template.mjs` (kept as a
  thin alias); the receipt schema in `releases/README.md` is updated.

## Enforcement map

`docs/docs/governance/delivery.mdx`: the D15 row already reads *have (CI)* for the witness gate;
this ADR is linked there as the batch-coverage sharpening.
