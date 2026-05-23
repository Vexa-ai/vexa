#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  pack-preflight.sh --pack-json <pack.json> --runtime-json <runtime.json> --out <preflight.json> [--repo-root <repo>] [--worktree <path>]

Rejects missing pack epic sections, forbidden/default ports, dirty bases, and
tests3 drift before a pack enters implementation.
USAGE
}

PACK_JSON=""
RUNTIME_JSON=""
OUT=""
REPO_ROOT=""
WORKTREE=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --pack-json) PACK_JSON="${2:-}"; shift 2 ;;
    --runtime-json) RUNTIME_JSON="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --repo-root) REPO_ROOT="${2:-}"; shift 2 ;;
    --worktree) WORKTREE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$PACK_JSON" ] || { echo "--pack-json is required" >&2; exit 2; }
[ -n "$RUNTIME_JSON" ] || { echo "--runtime-json is required" >&2; exit 2; }
[ -n "$OUT" ] || { echo "--out is required" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 2; }

if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(git rev-parse --show-toplevel)"
fi

errors=()
warnings=()

missing_sections="$(jq -r '.missing_sections[]? // empty' "$PACK_JSON" || true)"
if [ -n "$missing_sections" ]; then
  while IFS= read -r section; do
    [ -n "$section" ] && errors+=("missing pack epic section: $section")
  done <<< "$missing_sections"
fi

ports="$(jq -r '.ports | .. | numbers? // empty' "$RUNTIME_JSON" || true)"
for port in $ports; do
  case "$port" in
    3000|8056|8080) errors+=("forbidden/default port allocated: $port") ;;
  esac
done

tests3_status="$(git -C "$REPO_ROOT" status --porcelain -- tests3 || true)"
if [ -n "$tests3_status" ]; then
  errors+=("tests3 has local drift; tests3 is excluded from pack evidence")
fi

repo_status="$(git -C "$REPO_ROOT" status --porcelain || true)"
if [ -n "$repo_status" ]; then
  errors+=("repo root is dirty; create the pack worktree from a clean declared base before implementation")
fi

if [ -n "$WORKTREE" ] && [ -d "$WORKTREE/.git" ]; then
  worktree_status="$(git -C "$WORKTREE" status --porcelain || true)"
  if [ -n "$worktree_status" ]; then
    errors+=("pack worktree is dirty before implementation: $WORKTREE")
  fi
fi

if [ "${#errors[@]}" -eq 0 ]; then
  errors_json="[]"
else
  errors_json="$(printf '%s\n' "${errors[@]}" | jq -R . | jq -s .)"
fi
if [ "${#warnings[@]}" -eq 0 ]; then
  warnings_json="[]"
else
  warnings_json="$(printf '%s\n' "${warnings[@]}" | jq -R . | jq -s .)"
fi
status="pass"
[ "${#errors[@]}" -gt 0 ] && status="fail"

mkdir -p "$(dirname "$OUT")"
jq -n \
  --arg status "$status" \
  --arg pack_json "$PACK_JSON" \
  --arg runtime_json "$RUNTIME_JSON" \
  --arg repo_root "$REPO_ROOT" \
  --arg worktree "$WORKTREE" \
  --argjson errors "$errors_json" \
  --argjson warnings "$warnings_json" \
  '{status:$status, pack_json:$pack_json, runtime_json:$runtime_json, repo_root:$repo_root, worktree:$worktree, errors:$errors, warnings:$warnings}' \
  > "$OUT"

echo "wrote $OUT ($status)"
[ "$status" = "pass" ]
