# Stage: release-deliver-verify

**Level:** release · **Role:** verify · **Inner-loop:** `design → deliver → verify → sign`

| field        | value |
|--------------|-------|
| Actor        | mechanical + AI audit |
| Objective    | Prove public release artifacts match the signed staged artifact. |
| Inputs       | main merge commit, git tag, DockerHub/package artifacts, release notes, production handoff draft. |
| Outputs      | release verification note or machine report under `releases/<id>/`. |

## Steps

1. `lib/stage.py assert-is release-verify`.
2. Verify the merge commit, git tag, version file, release notes, and image tags.
3. Verify Docker image digests/package artifacts correspond to the intended
   release commit.
4. Verify the production handoff references only public release artifacts.

## Exit

Public artifacts are internally consistent and trace to what `stage-sign`
approved.

## May NOT

- Edit product code.
- Patch public artifacts silently; bounce to `release-deliver` if publication
  is incomplete or inconsistent.
- Deploy production.

## Next

`release-sign` — on clean verification.
`release-deliver` — on publication mismatch.

