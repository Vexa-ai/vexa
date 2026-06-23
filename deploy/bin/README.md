# deploy/bin — operator scripts for the dev loop

One concern: small, hand-run shell scripts that drive the **bbb dev loop** (deploy/iterate
against the live stack), not part of any service build.

- `redeploy-bot.sh` — fast app-layer rebuild of the bot image (`vexaai/vexa-bot:dev`) FROM the
  published env base, so a bot code change reaches bbb without the ~3.6 GB env rebuild.

Depends on: a working Docker + the repo checkout. Consumes nothing in `core/`; not imported by code.
