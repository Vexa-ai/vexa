# Release publication tooling

This directory contains release-time instruments that do not enter any Vexa
runtime image.

`image-names.mjs` is the one place a release image is named: `vexaai/vexa-<service>`. v0.13.3 is the
single transition release that also pushes each service image under its old `v012-<service>` repository
name in the same push (one digest); `LEGACY_DUAL_PUSH_ENDS_AFTER` stops it from v0.13.4.
`image-names.test.mjs` fails any surface that still writes an old name outside the frozen records.

`candidate-image-map.mjs` validates a frozen candidate map (twelve images under schema 4 with the
`vexa-` names, twelve under schema 3, eleven under schema 2, ten under schema 1, the last three with the
`v012-` names they were frozen with) and proves
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
