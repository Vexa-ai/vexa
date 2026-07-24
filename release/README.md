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
release workflow authenticates and audits the cancelled prior attempt, builds
only those two images, validates Bot against the frozen candidate stack and
Lite on native amd64+arm64, then emits their immutable identity receipt. Any
other partial set fails closed until it has an equivalent artifact-validation
path. Stable aliases are never moved by a replacement-candidate run.

The map records the top-level descriptor plus each selected platform manifest
and image-config digest, so production imageID evidence is compared at the
correct OCI identity layer.
