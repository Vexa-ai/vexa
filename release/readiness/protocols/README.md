# `release/readiness/protocols/` — the agent legs

Four of the six readiness legs are executed by a session rather than by a
command. These documents are their oracle: a leg's manifest entry points at one
of them, and the session follows it exactly.

| Protocol | Leg | Fires | Proves |
|---|---|---|---|
| [`blast-radius.md`](blast-radius.md) | 2 | formation | Every train PR mapped diff→surface, each surface carrying covering evidence, and every uncovered cell named rather than assumed |
| [`api-docs-sweep.md`](api-docs-sweep.md) | 3b | staging | Every endpoint the docs promise is probed against the staged candidate — errors and caps included, not only happy paths |
| [`security-review.md`](security-review.md) | 4 | both | New surfaces, authz, dependencies inside shipped images, secrets and injection, each finding carrying its own blocking verdict |
| [`compliance-review.md`](compliance-review.md) | 5 | both | Legal/privacy **and** architecture **and** principles **and** delivery process — the four-part scope is a founder ruling, not a reading |

Each protocol states its inputs, the `input_identity` its receipt must carry,
its method, its receipt, its blocking-verdict discipline, and the false greens
it has already produced.

**They are protocols, not checklists.** The method sections are written from the
runs that produced them so two sessions reviewing two candidates produce
comparable findings instead of two different improvisations. Where a run taught
that a technique was insufficient — inferring reachability instead of proving
it, reading a receipt instead of the run that wrote it, auditing a gate's output
instead of the gate — the correction is in the method, not in a footnote.

Two rules hold across all four:

- **"Founder decision needed" is `red`.** It is a blocking verdict with a named
  decision-holder, never a soft pass.
- **A re-cut candidate strands the receipt.** The identity binding is the
  point — re-run the leg, never re-stamp its receipt.

See [`../README.md`](../README.md) for the harness, the phases and the receipt
shape.
