- **Release runbook: publish step named (#714).** `releases/README.md` §the-two-phase-flow gains
  step 4 — after `:v012` moves, a human publishes the GitHub Release with `gh release create
  vX.Y.Z --verify-tag --latest --notes-file <notes>`; the guard's retract-to-draft re-check now
  lives under the act it guards. An operator ships a release end-to-end from the runbook alone.
