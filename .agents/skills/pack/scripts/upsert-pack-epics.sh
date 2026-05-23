#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'USAGE'
Usage:
  upsert-pack-epics.sh --proposal <pack-proposals.json> --out-dir <dir> [--repo OWNER/REPO] [--apply]

Dry-run by default. With --apply, creates pack epic issues for proposals that
do not already set existing_issue_number. Updating existing issues is supported
when existing_issue_number is present in the proposal.
USAGE
}

REPO="Vexa-ai/vexa"
PROPOSAL=""
OUT_DIR=""
APPLY="0"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --proposal) PROPOSAL="${2:-}"; shift 2 ;;
    --out-dir) OUT_DIR="${2:-}"; shift 2 ;;
    --apply) APPLY="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$PROPOSAL" ] || { echo "--proposal is required" >&2; exit 2; }
[ -n "$OUT_DIR" ] || { echo "--out-dir is required" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 2; }

mkdir -p "$OUT_DIR/bodies"
MANIFEST="$OUT_DIR/upsert-manifest.jsonl"
: > "$MANIFEST"

count="$(jq '.packs | length' "$PROPOSAL")"
for i in $(seq 0 $((count - 1))); do
  pack_id="$(jq -r ".packs[$i].pack_id" "$PROPOSAL")"
  title="$(jq -r ".packs[$i].title" "$PROPOSAL")"
  milestone="$(jq -r ".packs[$i].milestone // empty" "$PROPOSAL")"
  issue_number="$(jq -r ".packs[$i].existing_issue_number // empty" "$PROPOSAL")"
  body="$OUT_DIR/bodies/$pack_id.md"
  "$SCRIPT_DIR/render-epic-body.py" --proposal "$PROPOSAL" --pack-id "$pack_id" --out "$body" >/dev/null

  if [ "$APPLY" != "1" ]; then
    jq -cn --arg action dry_run --arg pack_id "$pack_id" --arg title "$title" --arg body "$body" \
      '{action:$action, pack_id:$pack_id, title:$title, body:$body}' >> "$MANIFEST"
    continue
  fi

  if [ -n "$issue_number" ]; then
    gh issue edit "$issue_number" -R "$REPO" --title "[Pack] $title" --body-file "$body"
    action="updated"
  else
    args=(issue create -R "$REPO" --title "[Pack] $title" --body-file "$body")
    [ -n "$milestone" ] && args+=(--milestone "$milestone")
    url="$(gh "${args[@]}")"
    issue_number="$url"
    action="created"
  fi
  jq -cn --arg action "$action" --arg pack_id "$pack_id" --arg title "$title" --arg issue "$issue_number" --arg body "$body" \
    '{action:$action, pack_id:$pack_id, title:$title, issue:$issue, body:$body}' >> "$MANIFEST"
done

echo "wrote $MANIFEST"
