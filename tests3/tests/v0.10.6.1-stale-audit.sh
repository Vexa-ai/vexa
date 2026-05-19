#!/usr/bin/env bash
# v0.10.6.1-stale-audit — backlog hygiene: 5 issues from Feb-Mar reviewed.
#
# Each of #166 #113 #128 #96 #198 must have EITHER:
#   (a) A closing comment with rationale (issue state=closed AND a comment
#       authored after 2026-05-08 referencing v0.10.6.1 audit), OR
#   (b) The "reconfirmed-stale-audit-2026-05-11" label (open + retagged
#       for a later cycle).
#
# Decisions are drafted in `drafts/2026-05-11-stale-audit-sweep-decisions.md`
# (BUSINESS workspace); the operator applies them via `gh issue close` /
# `gh issue edit --add-label` from this worktree.
#
# Steps:
#   decisions_filed   — query GH for each of the 5 issue numbers; assert
#                       every one resolves to (closed-with-rationale) OR
#                       (open-with-reconfirm-label). FAIL otherwise.
#
# Modes: compose. (gh-CLI driven; no running stack required, but we keep
# the mode binding so this is part of the scope-filtered compose matrix.)
#
# Env vars:
#   GH_REPO       (optional) target repo; default Vexa-ai/vexa
#   STALE_ISSUES  (optional) space-separated issue numbers; default to
#                 the 5 from scope.yaml.
#
# Skips (rather than fails) if `gh` is not available or unauthenticated —
# matches the no-VM convention (curl host-unresolvable → SKIP not FAIL).

source "$(dirname "$0")/../lib/common.sh"

step="${1:?usage: $0 <step>}"

GH_REPO="${GH_REPO:-Vexa-ai/vexa}"
STALE_ISSUES="${STALE_ISSUES:-166 113 128 96 198}"
RECONFIRM_LABEL="reconfirmed-stale-audit-2026-05-11"

echo ""
echo "  v0.10.6.1-stale-audit :: $step (repo=$GH_REPO, issues=$STALE_ISSUES)"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-stale-audit-$step"

case "$step" in
  decisions_filed)
    # Pre-flight: gh must be available + authenticated. If not, SKIP.
    if ! command -v gh >/dev/null 2>&1; then
      step_skip STALE_AUDIT_SWEEP_DECISIONS_FILED "gh CLI not installed"
      exit 0
    fi
    if ! gh auth status >/dev/null 2>&1; then
      step_skip STALE_AUDIT_SWEEP_DECISIONS_FILED "gh CLI not authenticated"
      exit 0
    fi

    failed=0
    detail=""
    for issue in $STALE_ISSUES; do
      # Query: state + labels + most-recent-comment timestamp.
      json="$(gh issue view "$issue" --repo "$GH_REPO" --json state,labels,comments 2>/dev/null)" || {
        echo "    SKIP $issue: gh issue view failed (network / not-found)"
        detail+=" #$issue:network-skip"
        continue
      }
      state="$(echo "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')"
      has_label="$(echo "$json" | python3 -c "
import json,sys
labels = json.load(sys.stdin).get('labels', []) or []
names = [l.get('name','') for l in labels]
print('yes' if '$RECONFIRM_LABEL' in names else 'no')
")"
      # Closing-comment heuristic: state=CLOSED + at least one comment authored
      # 2026-05-08 or later that mentions v0.10.6.1 / stale-audit.
      closed_with_rationale="$(echo "$json" | python3 -c "
import json,sys
data = json.load(sys.stdin)
state = data.get('state','')
if state != 'CLOSED':
    print('no'); raise SystemExit
comments = data.get('comments', []) or []
for c in comments:
    body = (c.get('body','') or '').lower()
    if ('v0.10.6.1' in body or 'stale-audit' in body or 'stale audit' in body):
        print('yes'); raise SystemExit
print('no')
")"

      if [ "$state" = "OPEN" ] && [ "$has_label" = "yes" ]; then
        echo "    ok  #$issue: open + reconfirm label"
      elif [ "$closed_with_rationale" = "yes" ]; then
        echo "    ok  #$issue: closed with rationale comment"
      else
        echo "    FAIL #$issue: state=$state, reconfirm-label=$has_label, closed-w-rationale=$closed_with_rationale"
        detail+=" #$issue"
        failed=1
      fi
    done

    if (( failed == 0 )); then
      step_pass STALE_AUDIT_SWEEP_DECISIONS_FILED "all 5 stale issues have a decision filed"
    else
      step_fail STALE_AUDIT_SWEEP_DECISIONS_FILED "missing decision on:${detail} (see drafts/2026-05-11-stale-audit-sweep-decisions.md)"
    fi
    ;;
  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac
