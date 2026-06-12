# ⚠️ STALE — monolith-era artifact (pre-0.11)

tests3 was built to validate what could not be verified alone: the whole-stack
engine (verbs: build · deploy · validate · provision · teardown · promote, the
MODE×ENV matrix) and an evidence registry of `proves:`/`symptom:` checks — in
0.11 terms, hundreds of module oracles written at the only level where
verification was possible. That level is dissolving.

**The MANIFEST (repo root, merged via MVP0 #442) supersedes this directory.**
Where tests3 and the MANIFEST disagree, the MANIFEST wins.

Migration (MANIFEST §3a/§7 trim ratchet):
- Evidence-registry checks migrate into brick oracles/fixtures as bricks extract,
  and are DELETED from registry.yaml as they move (MVP2+ trims).
- What ultimately remains at the deployment layer: the build/deploy/promote verbs
  and a thin boot smoke per car (MVP5 reduces tests3 to that).
- New build-orchestration needs (e.g. brick env-base images building before
  service images — packages/meet-join/Dockerfile.env) are specified against the
  MANIFEST, not against this engine's flows.

Do not extend this directory. Treat its contents as historical reference until
each piece is migrated or deleted at its MVP trim.
