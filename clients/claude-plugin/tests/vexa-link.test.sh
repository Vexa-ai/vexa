#!/bin/sh
# vexa-link against a fake Vexa: the line outlives a Vexa that does not answer and a stray refusal,
# and ends only on a refusal that holds. Run: sh tests/vexa-link.test.sh
set -u
here=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d); trap 'kill $srv 2>/dev/null; rm -rf "$work"' EXIT
# The fake answers each read from a script of status codes, one per request, then 200 forever.
cat > "$work/fake.py" <<'PY'
import http.server, json, sys, threading
plan = sys.argv[2].split(",") if sys.argv[2] else []
lock = threading.Lock()
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        with lock:
            code = int(plan.pop(0)) if plan else 200
        body = b"cursor 1\n" if "link/events" in self.path else json.dumps({"requests": [], "panels": []}).encode()
        self.send_response(code); self.end_headers()
        if code == 200: self.wfile.write(body)
    do_POST = do_GET
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
fail=0
run() {  # name plan seconds expect-exit
  port=$((20000 + $$ % 20000)); export HOME="$work/$1"; mkdir -p "$HOME"
  python3 "$work/fake.py" $port "$2" & srv=$!; sleep 0.5
  VEXA_LISTEN_KEY=k sh "$here/bin/vexa-link" "http://127.0.0.1:$port" > "$work/$1.out" 2>&1 & link=$!
  sleep "$3"
  if kill -0 $link 2>/dev/null; then got=running; touch "$HOME/.vexa/link/stop"; kill $link 2>/dev/null; wait $link 2>/dev/null
  else wait $link; got="exit$?"; fi
  kill $srv 2>/dev/null; wait $srv 2>/dev/null
  st=$(cat "$HOME/.vexa/link/state.json" 2>/dev/null)
  if [ "$got" = "$4" ]; then echo "ok   $1 ($got) $st"; else echo "FAIL $1: want $4, got $got $(cat "$work/$1.out") $st"; fail=1; fi
}
# Vexa does not answer for a while (a redeploy): the line waits and carries on.
run outage "502,502,502,502,502,502,502,502,502,502,502,502,502,502,502,502,502,502,502,502,502,502,502,502" 25 running
# One refusal while a gateway comes back: not the end.
run stray-refusal "401" 8 running
# The person revoked it: every read refused.
run revoked "401,401,401,401,401,401,401,401" 30 exit2
exit $fail
