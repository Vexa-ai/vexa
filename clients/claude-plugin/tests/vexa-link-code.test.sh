#!/bin/sh
# vexa-link spends listen_token's one-time code for the line's key (bug #7, card a5934431aa38; card e6cacb938f54):
# the key is then sent on every read, and is in no file, output or command line; a spent or expired code ends the start
# with its own word; a line already running spends no code; the older VEXA_LISTEN_KEY still starts.
# Run: sh tests/vexa-link-code.test.sh
set -u
here=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d); trap 'kill $srv 2>/dev/null; rm -rf "$work"' EXIT
cat > "$work/fake.py" <<'PY'
import http.server, json, sys
exchange = int(sys.argv[2])                 # how the exchange answers
seen = open(sys.argv[3], "a")
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        if self.path.endswith("/user/listen-code/exchange"):
            seen.write("exchange " + body + "\n"); seen.flush()
            self.send_response(exchange); self.end_headers()
            if exchange in (200, 201):
                self.wfile.write(json.dumps({"key": "KEY-BOUGHT-123"}).encode())
            return
        self.do_GET()
    def do_GET(self):
        seen.write("read " + str(self.headers.get("X-API-Key")) + "\n"); seen.flush()
        self.send_response(200); self.end_headers()
        if "workspace/file" in self.path:
            self.wfile.write(json.dumps({"path": "asks/in-this-call.md", "content": "---\nlines: 6\n---\n"}).encode())
        elif "link/events" in self.path:
            self.wfile.write(b"cursor 1\n")
        else:
            self.wfile.write(json.dumps({"requests": [], "panels": []}).encode())
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
fail=0
n=0
start() {  # name exchange-code env seconds expect
  n=$((n + 1)); port=$((21000 + ($$ + n) % 20000)); export HOME="$work/$1"; mkdir -p "$HOME"
  : > "$work/$1.seen"
  python3 "$work/fake.py" $port "$2" "$work/$1.seen" & srv=$!; sleep 0.5
  env $3 sh "$here/bin/vexa-link" "http://127.0.0.1:$port" > "$work/$1.out" 2>&1 & link=$!
  sleep "$4"
  if kill -0 $link 2>/dev/null; then got=running; touch "$HOME/.vexa/link/stop"; kill $link 2>/dev/null; wait $link 2>/dev/null
  else wait $link; got="exit$?"; fi
  kill $srv 2>/dev/null; wait $srv 2>/dev/null
  if [ "$got" = "$5" ]; then echo "ok   $1 ($got)"; else echo "FAIL $1: want $5, got $got $(cat "$work/$1.out")"; fail=1; fi
}
start code 201 "VEXA_LINK_CODE=abc123" 4 running
grep -q 'exchange {"code":"abc123"}' "$work/code.seen" && echo "ok   code spent at the exchange" || { echo "FAIL no exchange"; fail=1; }
grep -q '^read KEY-BOUGHT-123$' "$work/code.seen" && echo "ok   reads carry the bought key" || { echo "FAIL reads: $(sort -u "$work/code.seen")"; fail=1; }
if grep -rq 'KEY-BOUGHT-123' "$work/code/.vexa" "$work/code.out" 2>/dev/null; then echo "FAIL key in a file or the output"; fail=1; else echo "ok   key in no file and no output"; fi
start spent 409 "VEXA_LINK_CODE=abc123" 3 exit4
grep -q '^code-spent$' "$work/spent.out" && echo "ok   says code-spent" || { echo "FAIL spent word: $(cat "$work/spent.out")"; fail=1; }
start expired 410 "VEXA_LINK_CODE=abc123" 3 exit5
start legacy 500 "VEXA_LISTEN_KEY=OLDKEY" 4 running
grep -q '^read OLDKEY$' "$work/legacy.seen" && ! grep -q exchange "$work/legacy.seen" && echo "ok   an older key still starts, no exchange" \
  || { echo "FAIL legacy: $(sort -u "$work/legacy.seen")"; fail=1; }
start none 201 "X=1" 2 exit64
# a line already running spends no code
export HOME="$work/twice"; mkdir -p "$HOME/.vexa/link"; sleep 300 & holder=$!; echo $holder > "$HOME/.vexa/link/link.pid"
: > "$work/twice.seen"; port=$((22000 + $$ % 20000)); python3 "$work/fake.py" $port 201 "$work/twice.seen" & srv=$!; sleep 0.5
out=$(VEXA_LINK_CODE=abc123 sh "$here/bin/vexa-link" "http://127.0.0.1:$port")
kill $holder $srv 2>/dev/null; wait $srv 2>/dev/null
[ "$out" = "running $holder" ] && ! grep -q exchange "$work/twice.seen" && echo "ok   a running line spends no code" \
  || { echo "FAIL twice: $out $(cat "$work/twice.seen")"; fail=1; }
exit $fail
