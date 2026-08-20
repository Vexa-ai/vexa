# Release publication tooling

This directory contains release-time instruments that do not enter any Vexa
runtime image.

`candidate-image-map.mjs` validates a frozen ten-image candidate map and proves
that the source paths copied by each release Dockerfile are tree-identical to
that image's witnessed build source. Root-context images include
`.dockerignore`, because it shapes the bytes Docker receives. A difference is a
new candidate and blocks same-byte stable-tag publication.

When a witnessed candidate needs a repair, the planner permits only a
machine-validated partial path. Today that path is exactly Bot+Lite: the
release workflow uses Docker Hub's authenticated tag API to audit every image
ref from the cancelled prior attempt plus both replacement targets. Only a
conclusive 404 for every ref allows the build graph to start. It builds only
Bot and Lite, disables their shared registry-cache reads, validates Bot against
the frozen candidate stack and Lite on native amd64+arm64, then emits their
immutable identity receipt. Candidate validation reads public refs without
reusing the push account's exhausted pull quota. Any other partial set fails
closed until it has an equivalent artifact-validation path. Stable aliases are
never moved by a replacement-candidate run.

The map records the top-level descriptor plus each selected platform manifest
and image-config digest, so production imageID evidence is compared at the
correct OCI identity layer.

A map cannot exist before the build that publishes the digests it records, so
the freeze build defers its identity check with a notice and a human binds the
packet afterwards. That step is the review, and it is written down in
[`releases/README.md` § Bind, validate, freeze](../releases/README.md).
`release-validate-gating.test.mjs` holds both halves in place: the freeze path
may defer, and every promote / stable / already-mapped path still fails closed.

## `readiness/` — the six-leg production-readiness harness

`readiness/check.mjs` proves that a release's readiness coverage exists rather
than being asserted. Six legs cover a candidate — train value as of PRs, blast
radius as of PRs, full functionality (the validate legs plus API coverage as of
the docs), security, compliance, and the promotion ceremony — and
`releases/<version>/readiness.yaml` declares each one: whether a command or an
agent protocol is its oracle, where its receipt lives, what its receipt binds
to, and whether it fires at train formation, at staging, or both.

The runner checks every receipt a phase requires: present, parseable, GREEN, and
bound to the **current** candidate map sha. That binding is the mechanism.
Re-cutting a candidate rewrites `releases/<version>/candidate-images.json`, so
every receipt taken against the old bytes is stranded — the runner names those
legs and prints the reason the manifest declares for each, because a review of a
diff that is no longer shipping is not coverage.

Full operator documentation — phases, receipt shape, the agent protocols — is in
[`readiness/README.md`](readiness/README.md). The `readiness` job on
`release-validate` is **advisory on this train and blocking on the next**;
nothing `needs` it today, and it never substitutes for the witness or value
gates, which stay human.
