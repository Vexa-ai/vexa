### Dashboard build provenance

- **The Dashboard image is now built on an attested path.** `vexaai/dashboard` was the one image in
  the release set whose provenance could not be verified from the registry: it was hand-built on an
  operator's host and shipped with a classic `docker push`, which cannot carry an attestation, and
  its config carried no OCI labels at all. Provenance was recoverable only by SSH into that one
  machine, from a garbage-collected build cache. A new `dashboard-image` workflow builds and pushes
  it with SLSA provenance (`mode=max`) and an SBOM, stamps
  `org.opencontainers.image.revision`/`.source`/`.created`/`.version`, and fails the run if the
  attestation did not reach Docker Hub. `make push` gained the equivalent single-invocation buildx
  path for manual builds, and refuses a dirty tree. The Dashboard build is owned by `Vexa-ai/vexa`;
  it stays outside the OSS candidate map. See
  [`services/dashboard/README.md`](https://github.com/Vexa-ai/vexa/blob/main/services/dashboard/README.md).
- **The Dashboard image builds again.** Its Dockerfile still copied a root `VERSION` file that no
  longer exists in the tree, so any `docker build` of it failed at that layer; the release identity
  it feeds already falls back to the Helm chart `appVersion`. Pull requests touching
  `services/dashboard/**` now build the image in CI, so this cannot go unnoticed again.
