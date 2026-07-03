#!/usr/bin/env bash
# =============================================================================
# carve/seed.sh — ONE-TIME (or full-rebuild) history-preserving seed of vexa-core.
# Uses git filter-repo to extract the manifest's INCLUDE paths with FULL history,
# purge EXCLUDE sub-paths from history, normalize authors via mailmap, and ensure
# every commit is DCO-signed. Then applies overrides/transforms + docs as a final
# commit. Produces a force-pushable initial history.
#
# ⚠️  Force-pushes main. Run ONLY for the initial seed or a deliberate rebuild
#     BEFORE external contributors exist. After that, use sync.sh (append-only).
#
# Usage:  carve/seed.sh [--push]
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/manifest.sh"

WORK="$(mktemp -d)/seed"
echo "▶ cloning $MONO@$SRC_BRANCH → $WORK"
git clone -q --no-hardlinks "$MONO" "$WORK"
cd "$WORK"; git checkout -q "$SRC_BRANCH"

# --- filter-repo: include paths, mailmap, DCO sign-off injection -------------
INCLUDE_ARGS=(); for p in "${CARVE_INCLUDE[@]}"; do INCLUDE_ARGS+=(--path "$p"); done
git filter-repo --force "${INCLUDE_ARGS[@]}" --mailmap "$CARVE_MAILMAP" \
  --commit-callback '
msg = commit.message.decode("utf-8","replace")
name = commit.author_name.decode("utf-8","replace"); email = commit.author_email.decode("utf-8","replace")
if "Signed-off-by:" not in msg:
    commit.message = (msg.rstrip()+"\n\nSigned-off-by: "+name+" <"+email+">\n").encode("utf-8")
'
# --- purge EXCLUDE sub-paths from all history --------------------------------
EXCLUDE_ARGS=(); for p in "${CARVE_EXCLUDE[@]}"; do EXCLUDE_ARGS+=(--path "$p"); done
[ ${#EXCLUDE_ARGS[@]} -gt 0 ] && git filter-repo --force --invert-paths "${EXCLUDE_ARGS[@]}"

# --- final commit: overrides + transforms + docs -----------------------------
"$HERE/_apply_layer.sh" "$WORK"
git add -A
git -c user.name="Dmitry Grankin" -c user.email=39370484+DmitriyG228@users.noreply.github.com commit -s -q \
  -m "carve: apply open-core overrides (compose-only Makefile, transforms, docs)" || true

# --- checkpoint = the mono SHA this seed represents --------------------------
mkdir -p "$WORK/.carve"; git -C "$MONO" rev-parse "$SRC_BRANCH" > "$WORK/.carve/checkpoint"
git add .carve/checkpoint
git -c user.name="Dmitry Grankin" -c user.email=39370484+DmitriyG228@users.noreply.github.com commit -s -q \
  -m "carve: checkpoint $(git -C "$MONO" rev-parse --short "$SRC_BRANCH")"

echo "── seed result ──"; git shortlog -sne HEAD
echo "commits=$(git rev-list --count HEAD)  DCO=$(git log --format='%b'|grep -c Signed-off-by)"

if [ "${1:-}" = "--push" ]; then
  git remote remove origin 2>/dev/null || true; git remote add origin "$CARVE_REMOTE"
  git push --force origin HEAD:main
  echo "✓ force-pushed seed to $CARVE_REMOTE"
else
  echo "ℹ dry seed at $WORK — re-run with --push to publish"
fi
