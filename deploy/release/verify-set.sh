#!/usr/bin/env bash
# =============================================================================
# verify-set.sh — gate:release-set. Refuses unless EVERY image of the release
# set exists on Docker Hub at TAG with org.opencontainers.image.revision == SHA.
#
# This is the machine refusal behind "a release is the whole set, at one commit":
# the tag-push workflow runs it before the GitHub Release may publish, and it is
# runnable locally any time:  deploy/release/verify-set.sh vX.Y.Z [sha]
# (sha defaults to the commit the local tag points at).
# =============================================================================
set -euo pipefail
TAG="${1:?usage: verify-set.sh vX.Y.Z [sha]}"
SHA="${2:-$(git rev-list -n1 "$TAG" 2>/dev/null || true)}"
[ -n "$SHA" ] || { echo "no sha given and tag $TAG not found locally"; exit 1; }

IMAGES=(vexa-lite v012-admin-api v012-runtime v012-agent-worker v012-agent-api
        v012-meeting-api v012-gateway v012-mcp v012-terminal vexa-bot)

# .Image is a single image config for one-platform manifests and a platform-keyed
# map for multi-arch indexes — normalize both shapes and pull the revision label.
revision_of() {
  docker buildx imagetools inspect "$1" --format '{{json .Image}}' 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
imgs = list(d.values()) if isinstance(d,dict) and "config" not in d and "Config" not in d else [d]
for i in imgs:
    cfg = i.get("config") or i.get("Config") or {}
    lab = cfg.get("Labels") or cfg.get("labels") or {}
    r = lab.get("org.opencontainers.image.revision")
    if r: print(r); break
'
}

fail=0
for img in "${IMAGES[@]}"; do
  ref="vexaai/$img:$TAG"
  rev=$(revision_of "$ref")
  if [ -z "$rev" ]; then echo "MISSING  $ref (not on Hub or no revision label)"; fail=1
  elif [ "$rev" != "$SHA" ]; then echo "MISMATCH $ref revision=$rev expected=$SHA"; fail=1
  else echo "OK       $ref @ ${rev:0:12}"; fi
done
if [ "$fail" = 0 ]; then echo "gate:release-set — GREEN ($TAG @ ${SHA:0:12})"
else echo "gate:release-set — RED"; exit 1; fi
