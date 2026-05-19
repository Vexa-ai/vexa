#!/usr/bin/env bash
# v0.10.6.1-tts-pod — helm-deployed vexa-tts reaches Ready within 90s on
# a fresh cluster (model download path resilient).
#
# Steps:
#   pod_ready_after_first_boot
#     Static (always runs):
#       - chart template defines a vexa-tts deployment with a readiness probe.
#       - probe initialDelaySeconds / periodSeconds within sane envelope.
#         Voice downloads can legitimately take several minutes on a fresh PVC,
#         so readiness delay may be up to 300s; rollout still proves Ready.
#       - probe path is plausible (/health or /healthz).
#     Runtime (helm mode, if HELM_RELEASE state is present):
#       - kubectl rollout status deploy/<release>-vexa-tts --timeout=90s
#         succeeds.
#       - kubectl get pod ... shows Ready=True for at least one tts pod.
#     If helm context is missing (no STATE/helm_release / no kubectl), the
#     static checks alone determine the verdict; runtime portion is logged
#     as info, not failure.
#
# Mode: helm only.
#
# Why this check exists (#315 #308): TTS pod CrashLoopBackOff (154 restarts)
# on prod cluster surfaced in v0.10.6. 9fde7d2 probe-delay fix was
# incomplete; this guard pins the readiness invariant.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
CHART_DIR="$ROOT_DIR/deploy/helm/charts/vexa"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-tts-pod :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-tts-pod-$step"

case "$step" in
  pod_ready_after_first_boot)
    failed=0

    # ── STATIC: chart template defines readiness with bounded delays ──
    if ! command -v helm >/dev/null 2>&1; then
      echo "    SKIP static: helm not on PATH (CI image should have it)"
      step_skip TTS_POD_READY_AFTER_FIRST_BOOT "helm not installed"
      exit 0
    fi
    if [ ! -d "$CHART_DIR" ]; then
      echo "    FAIL static: chart dir missing at $CHART_DIR"
      step_fail TTS_POD_READY_AFTER_FIRST_BOOT "chart dir missing"
      exit 0
    fi

    rendered="$(helm template vexa "$CHART_DIR" 2>&1)" || {
      echo "    FAIL static: helm template failed"
      echo "$rendered" | tail -5 | sed 's/^/      /'
      step_fail TTS_POD_READY_AFTER_FIRST_BOOT "helm template failed"
      exit 0
    }

    # Extract the vexa-tts deployment block (heuristic: kind=Deployment +
    # name match). Then sub-extract readinessProbe and inspect delays.
    rendered_file="$(mktemp -t vexa-tts-rendered-XXXXXX.yaml)"
    printf '%s\n' "$rendered" > "$rendered_file"
    tts_block="$(python3 - "$rendered_file" <<'PY'
import sys, yaml
docs = list(yaml.safe_load_all(open(sys.argv[1])))
for d in docs:
    if not isinstance(d, dict): continue
    if d.get('kind') != 'Deployment': continue
    name = (d.get('metadata') or {}).get('name','') or ''
    if 'tts' not in name.lower(): continue
    print(yaml.dump(d))
    break
PY
)"
    rm -f "$rendered_file"

    if [ -z "$tts_block" ]; then
      echo "    FAIL: no Deployment named *tts* in rendered chart"
      failed=1
    else
      tts_block_file="$(mktemp -t vexa-tts-block-XXXXXX.yaml)"
      printf '%s\n' "$tts_block" > "$tts_block_file"
      readiness_ok="$(python3 - "$tts_block_file" <<'PY'
import sys, yaml, json
d = yaml.safe_load(open(sys.argv[1]))
containers = ((d.get('spec') or {}).get('template') or {}).get('spec') or {}
containers = containers.get('containers', []) or []
for c in containers:
    if 'tts' not in (c.get('name','') or '').lower():
        continue
    rp = c.get('readinessProbe') or {}
    if not rp:
        print(json.dumps({'ok': False, 'reason': 'no readinessProbe'}))
        raise SystemExit
    delay = int(rp.get('initialDelaySeconds') or 0)
    period = int(rp.get('periodSeconds') or 10)
    if delay > 300:
        print(json.dumps({'ok': False, 'reason': f'initialDelaySeconds={delay} > 300'}))
        raise SystemExit
    if period > 30:
        print(json.dumps({'ok': False, 'reason': f'periodSeconds={period} > 30'}))
        raise SystemExit
    # Path/exec check — accept either http (path) or exec/tcpSocket.
    if 'httpGet' in rp:
        path = (rp.get('httpGet') or {}).get('path','') or ''
        if not (path.startswith('/health') or path.startswith('/ready') or path in ('/', '/docs')):
            print(json.dumps({'ok': False, 'reason': f'httpGet.path={path!r} not plausible'}))
            raise SystemExit
    print(json.dumps({'ok': True, 'reason': f'delay={delay}s period={period}s'}))
    raise SystemExit
print(json.dumps({'ok': False, 'reason': 'no tts container in deployment'}))
PY
)"
      rm -f "$tts_block_file"
      ok="$(echo "$readiness_ok" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ok"])')"
      reason="$(echo "$readiness_ok" | python3 -c 'import json,sys; print(json.load(sys.stdin)["reason"])')"
      if [ "$ok" = "True" ]; then
        echo "    ok  static: readinessProbe configured ($reason)"
      else
        echo "    FAIL static: readinessProbe issue: $reason"
        failed=1
      fi
    fi

    # ── RUNTIME (helm mode only, optional) ──────────────────────
    HELM_RELEASE="$(state_read helm_release 2>/dev/null || true)"
    if [ -n "${HELM_RELEASE:-}" ] && command -v kubectl >/dev/null 2>&1 && kubectl cluster-info >/dev/null 2>&1; then
      deploy_name="${HELM_RELEASE}-vexa-tts-service"
      echo "    runtime check: kubectl rollout status deploy/$deploy_name --timeout=90s"
      rollout_out="$(kubectl rollout status "deploy/$deploy_name" --timeout=90s 2>&1)"
      rollout_rc=$?
      echo "$rollout_out" | tail -3 | sed 's/^/      /'
      if [ "$rollout_rc" -eq 0 ]; then
        ready_count="$(kubectl get pod -l "app.kubernetes.io/name=vexa,app.kubernetes.io/component=tts" -o json 2>/dev/null \
          | python3 -c "
import json,sys
data = json.load(sys.stdin)
ready = 0
for p in data.get('items', []) or []:
    conds = (p.get('status') or {}).get('conditions', []) or []
    if any(c.get('type')=='Ready' and c.get('status')=='True' for c in conds):
        ready += 1
print(ready)
" 2>/dev/null || echo 0)"
        if [ "${ready_count:-0}" -lt 1 ]; then
          ready_count="$(kubectl get pod -l "app.kubernetes.io/name=vexa,app.kubernetes.io/component=tts-service" -o json 2>/dev/null \
            | python3 -c "
import json,sys
data = json.load(sys.stdin)
ready = 0
for p in data.get('items', []) or []:
    conds = (p.get('status') or {}).get('conditions', []) or []
    if any(c.get('type')=='Ready' and c.get('status')=='True' for c in conds):
        ready += 1
print(ready)
" 2>/dev/null || echo 0)"
        fi
        if [ "${ready_count:-0}" -ge 1 ]; then
          echo "    ok  runtime: $ready_count tts pod(s) Ready"
        else
          echo "    FAIL runtime: no tts pods Ready after rollout"
          failed=1
        fi
      else
        echo "    FAIL runtime: rollout status timed out at 90s"
        failed=1
      fi
    else
      echo "    info: runtime check skipped (no helm release / no kubectl context)"
    fi

    if (( failed == 0 )); then
      step_pass TTS_POD_READY_AFTER_FIRST_BOOT "readinessProbe bounded + (runtime: pod Ready if cluster present)"
    else
      step_fail TTS_POD_READY_AFTER_FIRST_BOOT "one or more checks failed (see above)"
    fi
    ;;
  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac
