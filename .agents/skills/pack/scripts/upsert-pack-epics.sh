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

Applied pack epics are labeled "pack" and "status:available" so the develop
pipeline can claim exactly one available pack before implementation.
USAGE
}

REPO="Vexa-ai/vexa"
PROPOSAL=""
OUT_DIR=""
APPLY="0"
PACK_LABEL="pack"
AVAILABLE_LABEL="status:available"
IN_PROGRESS_LABEL="status:in-progress"
BLOCKED_LABEL="status:blocked"
DONE_LABEL="status:done"

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

ensure_label() {
  local name="$1"
  local color="$2"
  local description="$3"

  gh label create "$name" -R "$REPO" --color "$color" --description "$description" >/dev/null 2>&1 || true
}

ensure_pack_labels() {
  ensure_label "$PACK_LABEL" "5319e7" "Pack epic issue"
  ensure_label "$AVAILABLE_LABEL" "0e8a16" "Pack is available for develop to claim"
  ensure_label "$IN_PROGRESS_LABEL" "fbca04" "Pack is actively being developed"
  ensure_label "$BLOCKED_LABEL" "b60205" "Pack is blocked"
  ensure_label "$DONE_LABEL" "0e8a16" "Pack delivery is complete"
}

label_list_for_manifest() {
  printf '%s\n' "$@" | jq -R . | jq -s .
}

count="$(jq '.packs | length' "$PROPOSAL")"
for i in $(seq 0 $((count - 1))); do
  pack_id="$(jq -r ".packs[$i].pack_id" "$PROPOSAL")"
  title="$(jq -r ".packs[$i].title" "$PROPOSAL")"
  milestone="$(jq -r ".packs[$i].milestone // empty" "$PROPOSAL")"
  issue_number="$(jq -r ".packs[$i].existing_issue_number // empty" "$PROPOSAL")"
  body="$OUT_DIR/bodies/$pack_id.md"
  "$SCRIPT_DIR/render-epic-body.py" --proposal "$PROPOSAL" --pack-id "$pack_id" --out "$body" >/dev/null

  if [ "$APPLY" != "1" ]; then
    jq -cn \
      --arg action dry_run \
      --arg pack_id "$pack_id" \
      --arg title "$title" \
      --arg body "$body" \
      --argjson labels "$(label_list_for_manifest "$PACK_LABEL" "$AVAILABLE_LABEL")" \
      '{action:$action, pack_id:$pack_id, title:$title, body:$body, labels:$labels}' >> "$MANIFEST"
    continue
  fi

  ensure_pack_labels

  if [ -n "$issue_number" ]; then
    current_status_labels="$(gh issue view "$issue_number" -R "$REPO" --json labels --jq '.labels[].name | select(startswith("status:"))' || true)"
    edit_args=(issue edit "$issue_number" -R "$REPO" --title "[Pack] $title" --body-file "$body" --add-label "$PACK_LABEL")
    if [ -z "$current_status_labels" ]; then
      edit_args+=(--add-label "$AVAILABLE_LABEL")
      manifest_labels="$(label_list_for_manifest "$PACK_LABEL" "$AVAILABLE_LABEL")"
    else
      manifest_labels="$(printf '%s\n' "$PACK_LABEL" "$current_status_labels" | jq -R . | jq -s .)"
    fi
    gh "${edit_args[@]}"
    action="updated"
  else
    args=(issue create -R "$REPO" --title "[Pack] $title" --body-file "$body")
    [ -n "$milestone" ] && args+=(--milestone "$milestone")
    args+=(--label "$PACK_LABEL" --label "$AVAILABLE_LABEL")
    url="$(gh "${args[@]}")"
    issue_number="$url"
    action="created"
    manifest_labels="$(label_list_for_manifest "$PACK_LABEL" "$AVAILABLE_LABEL")"
  fi
  jq -cn \
    --arg action "$action" \
    --arg pack_id "$pack_id" \
    --arg title "$title" \
    --arg issue "$issue_number" \
    --arg body "$body" \
    --argjson labels "$manifest_labels" \
    '{action:$action, pack_id:$pack_id, title:$title, issue:$issue, body:$body, labels:$labels}' >> "$MANIFEST"
done

echo "wrote $MANIFEST"
