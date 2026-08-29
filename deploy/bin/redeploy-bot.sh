#!/bin/sh
# redeploy-bot.sh — fast app-layer rebuild of the per-meeting bot from the synced checkout.
#
# The bot is NOT a long-running container — the runtime kernel spawns it per meeting (BROWSER_IMAGE =
# vexaai/vexa-bot:v012). It is COMPILED (TS → dist + an esbuild browser bundle), so unlike the Python
# services it can't run from raw source; its dev loop is a FAST rebuild instead of a live mount:
#
#   git pull   →   deploy/bin/redeploy-bot.sh   →   the NEXT spawned bot uses the new image
#
# The heavy 3.6 GB env base (vexa/meet-join-env:dev) is published + layer-cached, so only the bot's app
# layer (COPY core + pnpm build) rebuilds. Run from anywhere in the checkout.
set -e
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
echo "▶ rebuilding vexaai/vexa-bot:v012 from $(git log --oneline -1) …"
# Stamp the build into the image: every captured-signal tape this bot writes carries it, and a
# staging/prod fixture is only attributable if it names the build that produced it.
DOCKER_BUILDKIT=1 docker build -f core/meetings/services/bot/Dockerfile \
  --build-arg VEXA_IMAGE_VERSION="$(git rev-parse --short HEAD)" \
  -t vexaai/vexa-bot:v012 .
echo "✓ vexaai/vexa-bot:v012 rebuilt — the next spawned bot picks it up (per-meeting; no container restart)."
