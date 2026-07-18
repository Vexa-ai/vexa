- **Per-PR lite boot smoke: lite-only breakage now fails the PR, not the release (#581).** A new
  `lite-smoke` job in `pr-value` builds `deploy/lite/Dockerfile.lite` from the PR's own tree, boots
  it with `make -C deploy/lite up`, probes the front doors, and runs the concurrent-bots smoke —
  scoped to PRs that touch `deploy/lite/**` or `core/**`, with no secrets (anonymous pulls). Three
  v0.12.2 release blockers were lite-only bugs this leg would have caught per-PR. A companion
  `gate:lite-makefile` statically rejects the exact footgun class (a comment line inside a
  `\`-continued recipe block in `deploy/lite/Makefile`, which once shipped `docker run` with an
  empty image).
