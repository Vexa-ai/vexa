# Official docs

Use only these sources for this skill:

- Deployment overview: https://github.com/Vexa-ai/vexa/blob/main/deploy/README.md
- Vexa Lite deploy README: https://github.com/Vexa-ai/vexa/blob/main/deploy/lite/README.md

Raw sources:

- https://raw.githubusercontent.com/Vexa-ai/vexa/main/deploy/README.md
- https://raw.githubusercontent.com/Vexa-ai/vexa/main/deploy/lite/README.md

## Required official checks

- Run from upstream repo root: `make lite`.
- Confirm documented endpoints:
  - Dashboard: `http://YOUR_IP:3000`
  - API docs: `http://YOUR_IP:8056/docs`
- Inspect health when needed:
  - `docker logs vexa-lite 2>&1 | grep -A15 "Post-Startup Health"`
  - `docker exec vexa-lite supervisorctl status`
- Stop with `make lite-down` only when stopping is requested.

## Hard limits

- Do not use docs.vexa.ai `docker run` variants.
- Do not use compose deployment as a Lite substitute.
- Do not invent additional service orchestration outside the Lite README.
