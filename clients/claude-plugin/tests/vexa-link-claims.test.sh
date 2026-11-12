#!/bin/sh
# vexa-link reports the desk roles this machine's chats hold (card brain:card/7d789ad1cee0): fresh, well-formed claims
# only, on every read of the relay, and keeps what the relay said about them. Run: sh tests/vexa-link-claims.test.sh
set -u
here=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d); trap 'kill $srv 2>/dev/null; rm -rf "$work"' EXIT
cat > "$work/fake.py" <<'PY'
import http.server, json, sys
log = open(sys.argv[2], "a")
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if "requests/next" in self.path:
            log.write(self.path + "\n"); log.flush()
            body = json.dumps({"requests": [], "present_until_ms": 1, "panels": [],
                               "roles": {"held": ["software"], "lost": [{"role": "ceo", "holder": "other-1", "since_ms": 9}],
                                         "refused": [], "unverified": []}}).encode()
        elif "link/events" in self.path:
            body = b"cursor 1\n"
        else:
            body = json.dumps({"path": "asks/in-this-call.md", "content": ""}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    do_POST = do_GET
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
fail=0
export HOME="$work/home"; R="$HOME/.vexa/link/roles"; mkdir -p "$R"
now=$(($(date +%s) * 1000)); old=$((now - 600000))
printf '{"session":"11111111-aaaa-4bbb-8ccc-000000000001","at_ms":1000,"seen":%s}' "$now" > "$R/software.json"
printf '{"session":"22222222-aaaa-4bbb-8ccc-000000000002","at_ms":2000,"seen":%s}' "$old" > "$R/billing.json"   # stale
printf '{"session":"bad session","at_ms":3000,"seen":%s}' "$now" > "$R/ceo.json"                                   # malformed
printf '{"session":"s3","at_ms":4000,"seen":%s}' "$now" > "$R/Bad.json"                                              # bad role
port=$((20000 + $$ % 20000))
python3 "$work/fake.py" $port "$work/paths.log" & srv=$!; sleep 0.5
VEXA_LISTEN_KEY=k sh "$here/bin/vexa-link" "http://127.0.0.1:$port" > "$work/out" 2>&1 & link=$!
sleep 3; touch "$HOME/.vexa/link/stop"; kill $link 2>/dev/null; wait $link 2>/dev/null
first=$(head -n 1 "$work/paths.log")
case "$first" in
  *"claims=software:11111111-aaaa-4bbb-8ccc-000000000001:1000") echo "ok   claims sends the one fresh, well-formed claim" ;;
  *) echo "FAIL claims: $first"; fail=1 ;;
esac
case "$first" in *billing*|*ceo*|*Bad*) echo "FAIL claims: a stale or malformed claim was sent: $first"; fail=1 ;; esac
st=$(cat "$HOME/.vexa/link/roles-status.json" 2>/dev/null)
case "$st" in *'"lost": [{"role": "ceo", "holder": "other-1"'*) echo "ok   roles-status keeps what the relay said" ;;
  *) echo "FAIL roles-status: $st"; fail=1 ;; esac
python3 -c 'import json,sys; json.loads(open(sys.argv[1]).read())' "$HOME/.vexa/link/roles-status.json" 2>/dev/null \
  && echo "ok   roles-status is JSON" || { echo "FAIL roles-status is not JSON"; fail=1; }
rm -f "$R"/*.json
VEXA_LISTEN_KEY=k sh -c ". /dev/null" ; : > "$work/paths.log"
python3 - "$here/bin/vexa-link" <<'PY' || fail=1
import re, sys
src = open(sys.argv[1]).read()
assert 'requests_url() { c=$(claims); echo "$api/agent/claude/requests/next?wait_ms=25000${c:+&claims=$c}"; }' in src
print("ok   no claims: the read is the old URL")
PY
exit $fail
