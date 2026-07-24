# Release publication tooling

This directory contains release-time instruments that do not enter any Vexa
runtime image.

`candidate-image-map.mjs` validates a frozen ten-image candidate map and proves
that the source paths copied by the release Dockerfiles are tree-identical to
the witnessed build source. A difference is a new candidate and blocks
same-byte stable-tag publication. The map records the top-level descriptor
plus each selected platform manifest and image-config digest, so production
imageID evidence is compared at the correct OCI identity layer.
