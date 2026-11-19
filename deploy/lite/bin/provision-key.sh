#!/bin/bash
# =============================================================================
# Vexa Lite — self-host API key provisioner, and the first-run sign-in link
# =============================================================================
# Runs in the background from the entrypoint. Once admin-api is up, mints (idempotently) a scoped
# API token for a self-host user and writes it to /run/vexa/key.env, then restarts the two UIs so
# they pick it up (their supervisord command sources that file). With zero-login on, that key is
# what a visitor without a session acts as; with it off (the default) it prints a one-time sign-in
# link for the self-host person instead.
#
# No-op when VEXA_API_KEY was supplied by the operator (their key wins) — login mode otherwise.
set -u

if [ -n "${VEXA_API_KEY:-}" ]; then
    echo "[provision-key] VEXA_API_KEY provided by operator; skipping self-host mint"
    exit 0
fi

# Inherited from the entrypoint, which mints one per boot when the operator supplied none.
ADMIN="${ADMIN_API_TOKEN:-}"
echo "[provision-key] waiting for admin-api..."
for _ in $(seq 1 60); do
    curl -sf -o /dev/null http://localhost:8001/health 2>/dev/null && break
    sleep 2
done

TOKS=$(ADMIN="$ADMIN" python3 - <<'PY'
import os, sys, json, urllib.request, urllib.error
admin = os.environ["ADMIN"]; B = "http://localhost:8001"
H = {"X-Admin-API-Key": admin, "Content-Type": "application/json"}
def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception:
        return 0, ""
s, b = call("GET", "/admin/users/email/self-host@vexa.ai")
if s == 200:
    uid = json.loads(b)["id"]
else:
    s, b = call("POST", "/admin/users", {"email": "self-host@vexa.ai", "name": "self host"})
    uid = json.loads(b)["id"]
# dashboard key (bot,tx)
s, b = call("POST", f"/admin/users/{uid}/tokens?scopes=bot,tx")
d = json.loads(b)
sys.stdout.write("VEXA_API_KEY=" + (d.get("token") or d.get("api_token") or "") + "\n")
# bot api key (bot,tx)
s, b = call("POST", f"/admin/users/{uid}/tokens?scopes=bot,tx")
d = json.loads(b)
sys.stdout.write("VEXA_BOT_API_KEY=" + (d.get("token") or d.get("api_token") or "") + "\n")
PY
)

if [ -n "$TOKS" ]; then
    # Root's alone: a new file under umask 077, renamed over any earlier one (which may predate that).
    rm -f /run/vexa/key.env.new
    ( umask 077; printf '%s\n' "$TOKS" > /run/vexa/key.env.new ) && mv -f /run/vexa/key.env.new /run/vexa/key.env
    supervisorctl -c /etc/supervisor/conf.d/vexa.conf restart vexa:dashboard vexa:terminal >/dev/null 2>&1 || true
    echo "[provision-key] self-host keys provisioned; dashboard + terminal restarted"
    # Sign-in is on (zero-login off, the default): the first-run door is a one-time link for the
    # self-host person, printed here for whoever reads the container log. Same token as an emailed
    # link; it works once and for an hour, and `vexa-signin-link` prints a fresh one.
    case "$(printf '%s' "${VEXA_TERMINAL_ZERO_LOGIN:-}" | tr '[:upper:]' '[:lower:]')" in
        true) ;;
        *)
            if LINK="$(/usr/local/bin/vexa-signin-link self-host@vexa.ai)"; then
                echo "[provision-key] ============================================================"
                echo "[provision-key] Sign in to the terminal (one use, valid for 1 hour):"
                echo "[provision-key]   $LINK"
                echo "[provision-key] A new link any time: docker exec <this container> vexa-signin-link"
                echo "[provision-key] ============================================================"
            else
                echo "[provision-key] WARN: no sign-in link printed (see the line above); sign in with a configured provider"
            fi ;;
    esac
else
    echo "[provision-key] WARN: could not mint keys — the UIs will require an interactive login"
fi
