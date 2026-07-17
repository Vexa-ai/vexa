# ADR 0033 — The two loops: debug ≠ deliver, flake is earned, the human appears twice (D-L cluster)

**Status:** accepted · 2026-07-16 · enforces **D1** (a rule that isn't enforced is a comment) and
records the **`D-L`** principle cluster (D-L0–D-L4) added to the delivery constitution · arms:
[#689](https://github.com/Vexa-ai/vexa/issues/689),
[#690](https://github.com/Vexa-ai/vexa/issues/690),
[#691](https://github.com/Vexa-ai/vexa/issues/691) · from the **v0.12.9 release retrospective**
(2026-07-16)

## Context

v0.12.9 burned ~12h of wall time where ~6h was achievable, and shipped six bugs that CI missed and
only the live witness caught. The retrospective traced almost every lost hour to one root cause:
**the release pipeline was used as a debugger.** Fixes were serially found and each packaged through
a full PR → gates → tag → build cycle (~40 min/iter) to discover whether the *next* layer worked —
instead of being proven on a hot loop that carries the real external semantics (seconds–minutes/iter)
and only *then* packaged.

The D-book codified PREPARE (§6) and PROOF (§8, was §7) but was thin on the DELIVER arc's *inner
mechanics* — how an agent actually drives a diff to the point where it is provable. Four concrete
failures anchor the gap, all from the v0.12.9 batch on `origin/main`:

- **Pipeline-as-debugger tax.** v0.12.8 was cut after four serially-found helm fixes with no
  assembled-system probe, and the 5th bug was in the new code. PR #684 emitted `containers: [{name}]`
  in every `kubectl run --overrides`, which merges the containers list *by replacement* — wiping the
  generated image/env/command, so the API server rejected every Pod (`spec.containers[0].image:
  Required value`) and every spawn died in ~0.3 s, forcing a full re-cut to v0.12.9. The eventual hot
  loop (a ConfigMap overlay of the runtime env + a dead-URL spawn probe) proved the whole chain in
  ~10 minutes — the thing four ~40-min pipeline iterations never touched.
- **Serial onion-peeling.** The helm chain was peeled one bug at a time (#656 → #675/#681 →
  #677/#680 → #676/#679 → #684), ~3h a single 20-min full-journey probe + one all-component log
  sweep would have inventoried at once.
- **Un-earned flake.** The `gate:helm` `printf | grep -q` under `set -euo pipefail` SIGPIPE-raced to
  exit 141 *exactly on a match* — deterministic on ubuntu runners once `$RENDER_AUTH` outgrew the
  pipe buffer. It "failed the v0.12.9 preflight twice" and was rerun as a flake — two full builds
  burned — before the code was read (#686).
- **Human misplacement.** ~8 owner round-trips were spent on dead tunnels, stale browser sessions,
  and a cold-start whisper model — a demo path the agent had not rehearsed end-to-end.

## Decision

Add a Part I principle cluster **`D-L` (the loops)** as **§7 "The two loops — debug before you
package"** (between §6 The issue and the renumbered §8 Proof), and a matching Part II operating
module **"Debugging — the hot loop"** immediately before the Validation ladder — Validation is
machine-first *proof*; the module is how a provable diff is *produced*. Five principles:

- **D-L0 — Two loops: debug ≠ deliver.** The release pipeline is a *packaging* loop; a hypothesis
  drops to the hottest loop that carries the real external semantics; ceremony packages proven diffs
  only.
- **D-L1 — Debug exit criterion:** a green end-to-end probe of the *assembled* system on the target
  surface — never "N green unit fixes".
- **D-L2 — Parallel failure inventory before fixing:** the full-journey probe matrix + one
  all-component log sweep, before the first fix.
- **D-L3 — Flake discipline:** "flake" claims nondeterminism and must be earned — one unexplained
  failure buys one rerun, an identical second demands reading the code; tool answers are
  cross-checked.
- **D-L4 — Human placement:** the human appears exactly twice (final witness; sign/approve), the
  agent self-serves everything else and rehearses the demo path end-to-end before the human walks it.

The DELIVER arc label in the Loop diagram gains `D-L` (`5. DELIVER (D-A · D6b · D-A2 · D-L)`). The
`D-L3` rule is reinforced in the TAKE protocol's honesty rules; the `D-L4` rehearsal obligation is
reinforced at choke point 2. AGENTS.md "How you work the checkout" links the section.

## Consequences

- **The next release cannot legally use the packaging pipeline to answer "does this code work?"**,
  cannot call a deterministic failure a flake, and cannot burn owner round-trips on an un-rehearsed
  demo — each rule now named in the constitution and mapped to an arm rather than living as prose
  that rots (D1).
- **The arms carry the machinery (D1).** #689 (a pre-tag real-spawn leg on k3s) and #690 (`make
  probe` — the standing full-journey hot loop per surface) are the mechanical gates for D-L0–D-L2 and
  the D-L4 rehearsal; #686's here-string regression test (`deploy/helm/tests/test_template.sh`) is
  the durable D-L3 guard; #691 removes the shared-file merge-train tax the same batch paid. Until the
  OPEN arms land, their enforcement-map rows read **TO BUILD**, honestly.
- **Not covered (honest):** the one-rerun flake discipline and the hot-loop *choice* stay
  behavioral — no machine forces an agent to overlay instead of rebuild; the arms make the hot loop
  cheap and standing, not mandatory. That residue is the same shape as every human-judgment gate in
  the book.
- **Renumbering:** inserting §7 shifts Proof → §8, People → §9, Release and closure → §10, How this
  document changes → §11; no cross-reference used section numbers (principles cite each other by ID
  and by named anchor), so only the visible heading numbers moved.

## Enforcement map

`docs/docs/governance/delivery.mdx`: five new rows — D-L0/D-L1 (**TO BUILD**, #689/#690),
D-L2 (**TO BUILD**, #690), D-L3 (**have** #686 regression test / **TO BUILD** rerun rule), and
D-L4 (**have** choke point 2 / **TO BUILD** #690 rehearsal probe).
