"""The bank profile's stand-ins for what a bank runs beside Vexa: python3 stub.py <mode>.

  model  an OpenAI-compatible chat/completions endpoint (no Anthropic endpoint anywhere)
  stt    a speech-to-text endpoint (OpenAI audio/transcriptions shape)
  idp    an OpenID Connect provider whose ID token carries no email_verified claim (ADFS)
  scc    an admission webhook that does to a Pod what OpenShift's restricted-v2 SCC does
  lb     the bank's TLS front door (an ingress or OpenShift edge route): HTTPS in, HTTP to LB_UPSTREAM

Every mode serves HTTPS with the certificate in /tls (tls.crt, tls.key), issued by the profile's
corporate CA. Standard library only, so one stock python image runs all four.
"""
import base64
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = sys.argv[1] if len(sys.argv) > 1 else ""
REPLY = "bank-profile stub model: the turn reached the in-cluster model."


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def int_bytes(n: int) -> bytes:
    return n.to_bytes((n.bit_length() + 7) // 8, "big")


# ── RSA for the IdP's RS256 ID tokens (PKCS#1 v1.5, SHA-256) ──────────────────────────────────────
def _der_ints(der: bytes) -> list:
    """The INTEGERs of a PKCS#1 RSAPrivateKey SEQUENCE: version, n, e, d, p, q, …"""
    def length(i):
        first = der[i]
        if first < 0x80:
            return first, i + 1
        k = first & 0x7F
        return int.from_bytes(der[i + 1:i + 1 + k], "big"), i + 1 + k
    assert der[0] == 0x30
    _, i = length(1)
    out = []
    while i < len(der):
        assert der[i] == 0x02
        n, i = length(i + 1)
        out.append(int.from_bytes(der[i:i + n], "big"))
        i += n
    return out


def load_rsa(path: str):
    pem = open(path).read()
    body = "".join(l for l in pem.splitlines() if l and not l.startswith("-----"))
    if "BEGIN RSA PRIVATE KEY" not in pem:
        raise SystemExit("idp: the signing key must be PKCS#1 (openssl genrsa -traditional)")
    _, n, e, d = _der_ints(base64.b64decode(body))[:4]
    return n, e, d


def rs256(n: int, d: int, signing_input: bytes) -> bytes:
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(signing_input).digest()
    k = (n.bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    return pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stdout.write("[%s] %s %s\n" % (MODE, self.address_string(), fmt % args))
        sys.stdout.flush()

    def body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def send(self, code: int, payload, ctype="application/json", headers=None):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        getattr(self, "get_" + MODE)()

    def do_POST(self):
        getattr(self, "post_" + MODE)()

    def do_PUT(self):
        self.proxy()

    def do_DELETE(self):
        self.proxy()

    def do_PATCH(self):
        self.proxy()

    # ── lb ──────────────────────────────────────────────────────────────────────────────────────
    def get_lb(self):
        self.proxy()

    def post_lb(self):
        self.proxy()

    def proxy(self):
        import http.client
        up = urllib.parse.urlparse(os.environ["LB_UPSTREAM"])
        conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=300)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("connection", "keep-alive")}
        headers["X-Forwarded-Proto"] = "https"
        headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        headers["X-Forwarded-For"] = self.client_address[0]
        conn.request(self.command, self.path, body=self.body() or None, headers=headers)
        r = conn.getresponse()
        self.send_response(r.status)
        chunked = r.getheader("Transfer-Encoding", "").lower() == "chunked"
        for k, v in r.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "keep-alive"):
                # a folded upstream header must not become a second header line downstream
                self.send_header(k.replace("\r", "").replace("\n", ""), v.replace("\r", "").replace("\n", ""))
        if chunked or r.getheader("Content-Length") is None:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        while True:
            chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()
        conn.close()

    # ── model ───────────────────────────────────────────────────────────────────────────────────
    def get_model(self):
        if self.path.rstrip("/").endswith("/models"):
            return self.send(200, {"object": "list", "data": [{"id": "bank-stub", "object": "model"}]})
        self.send(200, {"ok": True})

    def post_model(self):
        req = json.loads(self.body() or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self.send(404, {"error": {"message": "not here"}})
        model = req.get("model") or "bank-stub"
        tools = req.get("tools") or []
        usage = {"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22}
        if req.get("tool_choice") == "required" and tools:
            # The harness's one-off tool-calling check: answer with a call to the tool it offered.
            name = tools[0].get("function", {}).get("name", "noop")
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                 "function": {"name": name, "arguments": "{}"}}]}
            return self.send(200, {"id": "c1", "object": "chat.completion", "model": model,
                                   "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls"}],
                                   "usage": usage})
        if not req.get("stream"):
            return self.send(200, {"id": "c1", "object": "chat.completion", "model": model,
                                   "choices": [{"index": 0, "message": {"role": "assistant", "content": REPLY},
                                                "finish_reason": "stop"}], "usage": usage})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        frames = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": REPLY[:20]}}]},
                  {"choices": [{"index": 0, "delta": {"content": REPLY[20:]}}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                  {"choices": [], "usage": usage}]
        for f in frames:
            f.update({"id": "c1", "object": "chat.completion.chunk", "model": model})
            self.wfile.write(b"data: " + json.dumps(f).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

    # ── stt ─────────────────────────────────────────────────────────────────────────────────────
    def get_stt(self):
        self.send(200, {"status": "ok", "service": "bank-profile stt stub"})

    def post_stt(self):
        self.body()
        self.send(200, {"text": "bank profile stub transcript", "language": "en", "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "bank profile stub transcript"}]})

    # ── idp ─────────────────────────────────────────────────────────────────────────────────────
    CODES: dict = {}

    def issuer(self) -> str:
        return os.environ["IDP_ISSUER"].rstrip("/")

    def get_idp(self):
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        iss = self.issuer()
        if url.path.endswith("/.well-known/openid-configuration"):
            return self.send(200, {
                "issuer": iss, "authorization_endpoint": iss + "/authorize", "token_endpoint": iss + "/token",
                "jwks_uri": iss + "/keys", "userinfo_endpoint": iss + "/userinfo",
                "response_types_supported": ["code"], "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"],
                "scopes_supported": ["openid", "email", "profile", "allatclaims"],
                "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
                "code_challenge_methods_supported": ["S256"],
                # what ADFS advertises: no email_verified among the claims
                "claims_supported": ["sub", "iss", "aud", "exp", "iat", "nonce", "email", "name", "upn"]})
        if url.path.endswith("/keys"):
            n, e, _ = KEY
            return self.send(200, {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "bank-profile",
                                             "n": b64u(int_bytes(n)), "e": b64u(int_bytes(e))}]})
        if url.path.endswith("/authorize"):
            # The person is already signed in to the directory: approve at once, as ADFS with
            # Windows integrated auth does.
            code = uuid.uuid4().hex
            Handler.CODES[code] = {"nonce": q.get("nonce"), "client_id": q.get("client_id"),
                                   "challenge": q.get("code_challenge"), "redirect_uri": q.get("redirect_uri")}
            sep = "&" if "?" in q["redirect_uri"] else "?"
            loc = q["redirect_uri"] + sep + urllib.parse.urlencode({"code": code, "state": q.get("state", "")})
            return self.send(302, b"", "text/plain", {"Location": loc})
        if url.path.endswith("/userinfo"):
            return self.send(200, {"sub": os.environ["IDP_SUB"]})
        self.send(404, {"error": "not_found"})

    def post_idp(self):
        if not self.path.endswith("/token"):
            return self.send(404, {"error": "not_found"})
        form = dict(urllib.parse.parse_qsl(self.body().decode()))
        auth = self.headers.get("Authorization", "")
        client_id, secret = form.get("client_id"), form.get("client_secret")
        if auth.startswith("Basic "):
            client_id, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
            client_id, secret = urllib.parse.unquote(client_id), urllib.parse.unquote(secret)
        grant = Handler.CODES.pop(form.get("code", ""), None)
        if not grant or client_id != os.environ["IDP_CLIENT_ID"] or secret != os.environ["IDP_CLIENT_SECRET"]:
            return self.send(400, {"error": "invalid_grant"})
        if grant["challenge"]:
            verifier = form.get("code_verifier", "")
            if b64u(hashlib.sha256(verifier.encode()).digest()) != grant["challenge"]:
                return self.send(400, {"error": "invalid_grant", "error_description": "PKCE"})
        now = int(time.time())
        claims = {"iss": self.issuer(), "aud": client_id, "sub": os.environ["IDP_SUB"], "iat": now,
                  "exp": now + 600, "email": os.environ["IDP_EMAIL"], "name": os.environ["IDP_NAME"],
                  "upn": os.environ["IDP_EMAIL"]}
        if grant["nonce"]:
            claims["nonce"] = grant["nonce"]
        assert "email_verified" not in claims
        head = b64u(json.dumps({"alg": "RS256", "typ": "JWT", "kid": "bank-profile"}).encode())
        payload = b64u(json.dumps(claims).encode())
        n, _, d = KEY
        sig = b64u(rs256(n, d, f"{head}.{payload}".encode()))
        self.send(200, {"access_token": uuid.uuid4().hex, "token_type": "Bearer", "expires_in": 600,
                        "id_token": f"{head}.{payload}.{sig}"}, headers={"Cache-Control": "no-store"})

    # ── scc: OpenShift restricted-v2, as an admission webhook ─────────────────────────────────────
    def get_scc(self):
        self.send(200, {"ok": True})

    def post_scc(self):
        review = json.loads(self.body())
        req = review["request"]
        allowed, message, patch = scc_admit(req["object"])
        resp = {"uid": req["uid"], "allowed": allowed}
        if not allowed:
            resp["status"] = {"code": 403, "message": message}
        elif patch:
            resp["patchType"] = "JSONPatch"
            resp["patch"] = base64.b64encode(json.dumps(patch).encode()).decode()
        print("[scc] %s %s/%s %s" % ("ADMIT" if allowed else "DENY", req.get("namespace"),
                                     req["object"].get("metadata", {}).get("name") or
                                     req["object"].get("metadata", {}).get("generateName"), message))
        sys.stdout.flush()
        self.send(200, {"apiVersion": "admission.k8s.io/v1", "kind": "AdmissionReview", "response": resp})


def scc_admit(pod: dict):
    """restricted-v2 (OpenShift 4.11+): refuse what it refuses, then inject what it injects.

    The namespace's uid range starts at SCC_UID (random per profile run, like OpenShift's
    openshift.io/sa.scc.uid-range annotation) and is 10000 wide. Containers named in SCC_RO get a
    read-only root filesystem: the containers the chart itself renders read-only."""
    uid = int(os.environ["SCC_UID"])
    ro = {c for c in os.environ.get("SCC_RO", "").split(",") if c}
    spec = pod["spec"]
    psc = dict(spec.get("securityContext") or {})
    problems = []
    for key in ("hostNetwork", "hostPID", "hostIPC"):
        if spec.get(key):
            problems.append(f"{key} is not allowed")
    for v in spec.get("volumes") or []:
        if "hostPath" in v:
            problems.append(f"volume {v['name']}: hostPath is not allowed")
    def in_range(u):
        return u is None or uid <= int(u) < uid + 10000
    if not in_range(psc.get("runAsUser")):
        problems.append(f"pod runAsUser {psc.get('runAsUser')} is outside the range [{uid}, {uid + 9999}]")
    if psc.get("fsGroup") is not None and not in_range(psc.get("fsGroup")):
        problems.append(f"fsGroup {psc.get('fsGroup')} is outside the range [{uid}, {uid + 9999}]")
    seccomp = (psc.get("seccompProfile") or {}).get("type")
    if seccomp not in (None, "RuntimeDefault"):
        problems.append(f"pod seccomp {seccomp} is not allowed (runtime/default only)")
    patch = []
    groups = [("containers", c) for c in spec.get("containers") or []] + \
             [("initContainers", c) for c in spec.get("initContainers") or []]
    idx = {"containers": 0, "initContainers": 0}
    for kind, c in groups:
        i = idx[kind]
        idx[kind] += 1
        sc = dict(c.get("securityContext") or {})
        name = c.get("name")
        if sc.get("privileged"):
            problems.append(f"container {name}: privileged is not allowed")
        if sc.get("allowPrivilegeEscalation") is True:
            problems.append(f"container {name}: allowPrivilegeEscalation true is not allowed")
        added = [a for a in ((sc.get("capabilities") or {}).get("add") or []) if a != "NET_BIND_SERVICE"]
        if added:
            problems.append(f"container {name}: capabilities {added} may not be added")
        if not in_range(sc.get("runAsUser")):
            problems.append(f"container {name}: runAsUser {sc.get('runAsUser')} is outside the range "
                            f"[{uid}, {uid + 9999}]")
        cseccomp = (sc.get("seccompProfile") or {}).get("type")
        if cseccomp not in (None, "RuntimeDefault"):
            problems.append(f"container {name}: seccomp {cseccomp} is not allowed (runtime/default only)")
        sc.setdefault("runAsUser", uid)
        sc["runAsNonRoot"] = True
        sc["allowPrivilegeEscalation"] = False
        caps = dict(sc.get("capabilities") or {})
        caps["drop"] = ["ALL"]
        sc["capabilities"] = caps
        if name in ro:
            sc["readOnlyRootFilesystem"] = True
        patch.append({"op": "add", "path": f"/spec/{kind}/{i}/securityContext", "value": sc})
    if problems:
        return False, "unable to validate against restricted-v2: " + "; ".join(problems), None
    psc.setdefault("fsGroup", uid)
    psc["runAsNonRoot"] = True
    psc["seccompProfile"] = {"type": "RuntimeDefault"}
    patch.insert(0, {"op": "add", "path": "/spec/securityContext", "value": psc})
    return True, f"uid {uid}", patch


if __name__ == "__main__":
    if MODE not in ("model", "stt", "idp", "scc", "lb"):
        raise SystemExit(__doc__)
    KEY = load_rsa("/idp-key/key.pem") if MODE == "idp" else None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain("/tls/tls.crt", "/tls/tls.key")
    srv = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8443"))), Handler)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    print(f"[{MODE}] listening on :{srv.server_address[1]}")
    sys.stdout.flush()
    srv.serve_forever()
