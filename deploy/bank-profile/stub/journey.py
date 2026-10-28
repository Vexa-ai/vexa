"""The bank profile's user side, run from a workstation Pod outside the release namespace:

  python3 journey.py signin                 first sign-in through the corporate IdP (OIDC)
  python3 journey.py workspace              create the person's workspace           (needs KEY)
  python3 journey.py chat                   one agent chat turn against the stub model (needs KEY)
  python3 journey.py meeting                a meeting bot to `active`, with a transcript segment (needs KEY)

Env: TERMINAL (the terminal's URL), GATEWAY (the gateway's URL), KEY (an API key of the person who
signed in), SSL_CERT_FILE (the corporate CA). Each step prints `RESULT <step> PASS|FAIL <detail>` last,
and exits 0 only on PASS. Standard library only.
"""
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

T = os.environ.get("TERMINAL", "").rstrip("/")
G = os.environ.get("GATEWAY", "").rstrip("/")
KEY = os.environ.get("KEY", "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR), NoRedirect())


def call(method, url, data=None, headers=None, timeout=60):
    body = None
    headers = dict(headers or {})
    if isinstance(data, dict) and headers.get("Content-Type") == "application/x-www-form-urlencoded":
        body = urllib.parse.urlencode(data).encode()
    elif data is not None:
        body = json.dumps(data).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        r = OPENER.open(req, timeout=timeout)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def result(step, ok, detail):
    print(f"RESULT {step} {'PASS' if ok else 'FAIL'} {detail}")
    sys.exit(0 if ok else 1)


def api(method, path, data=None, timeout=60):
    return call(method, G + path, data, {"X-API-Key": KEY}, timeout)


def signin():
    code, _, raw = call("GET", T + "/api/auth/providers")
    providers = json.loads(raw or b"{}") if code == 200 else {}
    print("providers:", code, sorted(providers))
    if "oidc" not in providers:
        result("signin", False, f"the terminal offers no oidc door: {code} {raw[:200]!r}")
    code, _, raw = call("GET", T + "/api/auth/csrf")
    csrf = json.loads(raw)["csrfToken"]
    code, hdr, raw = call("POST", T + "/api/auth/signin/oidc",
                          {"csrfToken": csrf, "callbackUrl": T + "/", "json": "true"},
                          {"Content-Type": "application/x-www-form-urlencoded"})
    url = (json.loads(raw).get("url") if code == 200 else hdr.get("Location")) or ""
    print("signin/oidc:", code, url.split("?")[0])
    if "/authorize" not in url:
        result("signin", False, f"signin did not send the browser to the IdP: {code} {raw[:300]!r}")
    hops = 0
    while url and hops < 8:
        hops += 1
        code, hdr, raw = call("GET", url)
        nxt = hdr.get("Location") or ""
        print(f"hop {hops}: {code} {url.split('?')[0]} -> {nxt.split('?')[0]}")
        if "error" in nxt:
            result("signin", False, f"the terminal refused the sign-in: {urllib.parse.unquote(nxt)}")
        if not nxt:
            break
        url = urllib.parse.urljoin(url, nxt)
        if url.rstrip("/") == T:
            break
    code, _, raw = call("GET", T + "/api/auth/session")
    session = json.loads(raw or b"{}")
    email = (session.get("user") or {}).get("email")
    print("session:", code, json.dumps(session)[:300])
    result("signin", bool(email) and email == os.environ.get("EXPECT_EMAIL"),
           f"signed in as {email} through the IdP; cookies {sorted(c.name for c in JAR)}")


def workspace():
    code, _, raw = api("POST", "/agent/workspace/init")
    print("workspace/init:", code, raw[:300])
    if code not in (200, 201):
        result("workspace", False, f"POST /agent/workspace/init -> {code} {raw[:300]!r}")
    code, _, raw = api("GET", "/agent/workspace/tree")
    print("workspace/tree:", code, raw[:300])
    result("workspace", code == 200, f"init {json.loads(raw or b'{}') and 'ok'}; tree -> {code}")


def chat():
    req = urllib.request.Request(G + "/agent/chat", method="POST",
                                 data=json.dumps({"prompt": "Say hello from the bank profile."}).encode(),
                                 headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    frames, done = [], None
    try:
        with OPENER.open(req, timeout=300) as r:
            for line in r:
                line = line.decode().strip()
                if not line.startswith("data:"):
                    continue
                try:
                    f = json.loads(line[5:])
                except ValueError:
                    continue
                frames.append(f.get("type"))
                if f.get("type") in ("error", "rejected"):
                    print("frame:", json.dumps(f)[:800])
                if f.get("type") == "done":
                    done = f
                    break
    except urllib.error.HTTPError as e:
        result("chat", False, f"POST /agent/chat -> {e.code} {e.read()[:300]!r}")
    print("frames:", frames)
    print("done:", json.dumps(done)[:600])
    ok = bool(done) and done.get("ok") is True and "stub model" in (done.get("reply") or "")
    result("chat", ok, f"done.ok={done and done.get('ok')} session={done and done.get('sessionId')} "
                       f"reply={(done or {}).get('reply')!r}")


def meeting():
    native = "bank-%d" % int(time.time())
    code, _, raw = api("POST", "/bots", {"platform": "google_meet", "native_meeting_id": native,
                                         "bot_name": "mock:normal", "transcribe_enabled": False})
    print("POST /bots:", code, raw[:300])
    if code not in (200, 201):
        result("meeting", False, f"POST /bots -> {code} {raw[:300]!r}")
    row, segments, seen = None, [], []
    deadline = time.time() + 240
    while time.time() < deadline:
        code, _, raw = api("GET", "/meetings")
        rows = [m for m in (json.loads(raw or b"{}").get("meetings") or [])
                if m.get("native_meeting_id") == native] if code == 200 else []
        row = rows[0] if rows else None
        if row and row.get("status") not in seen:
            seen.append(row.get("status"))
        code, _, raw = api("GET", f"/transcripts/google_meet/{native}")
        if code == 200:
            segments = json.loads(raw or b"{}").get("segments") or []
        if row and row.get("status") in ("completed", "failed") and segments:
            break
        time.sleep(1)
    trail = [t.get("to") for t in ((row or {}).get("data") or {}).get("status_transition") or []]
    print("row:", json.dumps(row)[:800])
    print("segments:", json.dumps(segments)[:600])
    ok = "active" in trail + seen and len(segments) >= 1 and (row or {}).get("status") == "completed"
    result("meeting", ok, f"statuses polled {seen}; trail {trail}; {len(segments)} segment(s); "
                          f"first {segments[0].get('text') if segments else None!r}")


if __name__ == "__main__":
    {"signin": signin, "workspace": workspace, "chat": chat, "meeting": meeting}[sys.argv[1]]()
