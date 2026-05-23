#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  claim-pack-epic.sh --issue <issue-or-url> --out <claim.json> [--repo OWNER/REPO] [--apply]

Dry-run by default. With --apply, verifies the GitHub issue is a pack epic with
status:available, then moves it to status:in-progress.
USAGE
}

REPO="Vexa-ai/vexa"
ISSUE=""
OUT=""
APPLY="0"
PACK_LABEL="pack"
AVAILABLE_LABEL="status:available"
IN_PROGRESS_LABEL="status:in-progress"
BLOCKED_LABEL="status:blocked"
DONE_LABEL="status:done"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --issue) ISSUE="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --apply) APPLY="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$ISSUE" ] || { echo "--issue is required" >&2; exit 2; }
[ -n "$OUT" ] || { echo "--out is required" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 2; }
command -v gh >/dev/null 2>&1 || { echo "gh is required" >&2; exit 2; }

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

issue_number() {
  local value="$1"
  if [[ "$value" =~ /issues/([0-9]+) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "$value" =~ ^#?([0-9]+)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  echo "cannot parse issue number from: $value" >&2
  return 2
}

number="$(issue_number "$ISSUE")"
issue_json="$(gh issue view "$number" -R "$REPO" --json number,title,url,state,labels)"
labels="$(printf '%s\n' "$issue_json" | jq -r '[.labels[].name]')"
has_pack="$(printf '%s\n' "$labels" | jq --arg label "$PACK_LABEL" 'index($label) != null')"
has_available="$(printf '%s\n' "$labels" | jq --arg label "$AVAILABLE_LABEL" 'index($label) != null')"
state="$(printf '%s\n' "$issue_json" | jq -r '.state')"
status_label_count="$(printf '%s\n' "$labels" | jq '[.[] | select(startswith("status:"))] | length')"

errors=()
[ "$state" = "OPEN" ] || errors+=("issue is not open; state is $state")
[ "$has_pack" = "true" ] || errors+=("issue is not labeled $PACK_LABEL")
[ "$has_available" = "true" ] || errors+=("issue is not labeled $AVAILABLE_LABEL")
[ "$status_label_count" = "1" ] || errors+=("issue must have exactly one status:* label; found $status_label_count")

status="pass"
[ "${#errors[@]}" -gt 0 ] && status="fail"
if [ "${#errors[@]}" -eq 0 ]; then
  errors_json="[]"
else
  errors_json="$(printf '%s\n' "${errors[@]}" | jq -R . | jq -s .)"
fi

mkdir -p "$(dirname "$OUT")"
jq -n \
  --arg action "$([ "$APPLY" = "1" ] && echo claim || echo dry_run)" \
  --arg status "$status" \
  --arg repo "$REPO" \
  --arg issue "$number" \
  --argjson issue_json "$issue_json" \
  --argjson labels "$labels" \
  --argjson errors "$errors_json" \
  '{action:$action,status:$status,repo:$repo,issue:$issue,issue_json:$issue_json,labels:$labels,errors:$errors}' \
  > "$OUT"

if [ "$status" != "pass" ]; then
  echo "cannot claim pack issue #$number: $(printf '%s; ' "${errors[@]}")" >&2
  exit 1
fi

if [ "$APPLY" != "1" ]; then
  echo "wrote $OUT (dry_run)"
  exit 0
fi

ensure_pack_labels

gh issue edit "$number" -R "$REPO" \
  --remove-label "$AVAILABLE_LABEL" \
  --add-label "$IN_PROGRESS_LABEL"

echo "claimed #$number: $AVAILABLE_LABEL -> $IN_PROGRESS_LABEL"
