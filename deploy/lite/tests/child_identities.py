"""Run INSIDE a booted Lite container (`make -C deploy/lite test` pipes it to `docker exec -i … python3 -`):
start children through the runtime the way agent-api and meeting-api do, and check what each one is.

* an agent worker for a numeric subject, one for a named subject, a meeting bot: every process the
  runtime starts (and everything those start) runs with no uid or gid 0 anywhere, no_new_privs set,
  and no supplementary group; a worker as its subject's uid, a bot as a uid of its own;
* a dispatch whose subject cannot be mapped is refused, and starts nothing;
* as each identity a child ran with: no other process's environment is readable, nor root's state
  (root's runtime directory, the rendered supervisor config, Valkey's config and data, the self-host
  API keys, the signing key, the workload logs, the VNC password, the mounted model credential), and
  the browser install and the workspace store are not writable; and for every uid at once, each of
  those paths is root's with no group or other read or write bit;
* there is no shared X display (each bot starts its own: tests/bot_displays.py checks those, with two
  bots running) and nothing answers on the VNC or noVNC port;
* when a chat turn can start (model credentials configured), the turn's prompt appears on no
  process's command line while its CLI runs;
* no process in the container carries a service secret on its command line.

Stdlib only; runs as root; never prints a value. Exits 1 naming what failed.
"""
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

RUNTIME = "http://127.0.0.1:8090"
ADMIN = "http://127.0.0.1:8001"
GATEWAY = "http://127.0.0.1:8056"
RENDERED = "/run/vexa/supervisord.conf"
CONF = "/etc/supervisor/conf.d/vexa.conf"
UID_BASE, SUBJECT_UID_BASE, WORKLOAD_UID_BASE = 100000, 1_000_000_000, 1_500_000_000
ROOT_ONLY = ["/run/vexa", "/run/vexa/supervisord.conf", "/run/vexa/valkey.conf", "/run/vexa/key.env",
             "/var/lib/redis",
             "/var/lib/vexa/state/identity/signing-key.pem", "/var/lib/vexa/state/nextauth-secret",
             "/var/lib/vexa-runtime/logs", "/var/lib/vexa/host-claude"]
NOT_WRITABLE = ["/ms-playwright", "/workspaces", "/app", "/usr/local/bin"]
SECRET_NAME = re.compile(r"SECRET|TOKEN|PASSWORD|_KEY$")
WATCH_SECONDS = 20


def environ_of(pid: int) -> dict:
    with open(f"/proc/{pid}/environ", "rb") as f:
        return dict(e.split(b"=", 1) for e in f.read().split(b"\0") if b"=" in e)


def runtime_token() -> str:
    text = open(RENDERED, encoding="utf-8").read()
    return re.search(r'RUNTIME_API_TOKEN="([^"]+)"', text).group(1)


def http(method: str, url: str, headers: dict, body=None, timeout: float = 30):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def call(method: str, path: str, token: str, body=None) -> int:
    return http(method, RUNTIME + path, {"Authorization": f"Bearer {token}"}, body)[0]


def runtime_pid() -> int:
    out = subprocess.run(["supervisorctl", "-c", CONF, "status"], capture_output=True, text=True).stdout
    return int(re.search(r"^\S*runtime\s+RUNNING\s+pid (\d+)", out, flags=re.M).group(1))


def status(pid: int) -> dict:
    fields = {}
    with open(f"/proc/{pid}/status", encoding="utf-8") as f:
        for line in f:
            key, _, value = line.partition(":")
            fields[key] = value.split()
    return {"uids": [int(v) for v in fields["Uid"]], "gids": [int(v) for v in fields["Gid"]],
            "groups": [int(v) for v in fields.get("Groups", [])], "nnp": fields.get("NoNewPrivs", ["?"])[0],
            "name": (fields.get("Name") or ["?"])[0]}


def parent(pid: int) -> int:
    with open(f"/proc/{pid}/stat", "rb") as f:
        return int(f.read().rsplit(b")", 1)[1].split()[1])


def cmdline(pid: int) -> bytes:
    with open(f"/proc/{pid}/cmdline", "rb") as f:
        return f.read()


def pids() -> list:
    return [int(p) for p in os.listdir("/proc") if p.isdigit()]


def descendants(root: int) -> list:
    parents = {}
    for pid in pids():
        try:
            parents[pid] = parent(pid)
        except OSError:
            pass
    out = []
    for pid in parents:
        node = pid
        while node in parents and node > 1:
            node = parents[node]
            if node == root:
                out.append(pid)
                break
    return out


class Watch(threading.Thread):
    """Records every process the runtime starts (and everything those start), as often as it can, for
    as long as it runs. A fork of the runtime that has not exec'd yet still runs the runtime's own code
    (it drops its identity just before exec), so it is recorded only once it runs something else."""

    def __init__(self, root: int) -> None:
        super().__init__(daemon=True)
        self.root, self.seen, self.stop = root, {}, threading.Event()
        self.own = cmdline(root)

    def run(self) -> None:
        while not self.stop.is_set():
            for pid in descendants(self.root):
                if pid not in self.seen:
                    try:
                        if cmdline(pid) == self.own:
                            continue
                        self.seen[pid] = status(pid)
                    except OSError:
                        pass
            time.sleep(0.005)


# ── VNC, as seen by an identity ──────────────────────────────────────────────────────────────────

def vnc_open(port: int) -> str:
    """'closed', 'password' (only authenticated security types), or 'OPEN' (no authentication)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            if port != 5900:
                return "listening"
            s.recv(12)
            s.sendall(b"RFB 003.008\n")
            n = s.recv(1)
            types = s.recv(n[0]) if n and n[0] else b""
            return "OPEN" if 1 in types else "password"
    except ConnectionRefusedError:
        return "closed"
    except OSError:
        return "closed"


def probe_as(uids: list, gid: int, groups: list, others: list, bot: bool) -> list:
    """Fork, become this identity, and try what a child must not be able to do. Returns failures."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        bad = []
        try:
            os.setgroups(groups)
            os.setgid(gid)
            os.setuid(uids[0])
            me = os.getuid()
            for other in others:
                try:
                    if os.stat(f"/proc/{other}").st_uid == me:
                        continue
                    open(f"/proc/{other}/environ", "rb").read(1)
                    bad.append(f"read the environment of pid {other}")
                except (PermissionError, FileNotFoundError, ProcessLookupError):
                    pass
            for path in ROOT_ONLY:
                try:
                    if os.path.isdir(path):
                        os.listdir(path)
                    else:
                        open(path, "rb").read(1)
                    bad.append(f"read {path}")
                except (PermissionError, FileNotFoundError):
                    pass
            for path in NOT_WRITABLE:
                try:
                    open(os.path.join(path, ".r4-probe"), "w").close()
                    os.unlink(os.path.join(path, ".r4-probe"))
                    bad.append(f"wrote into {path}")
                except OSError:
                    pass
            for port in (5900, 6080):
                if vnc_open(port) != "closed":
                    bad.append(f"something answers on the VNC port :{port}")
        except Exception as e:                       # noqa: BLE001 — reported, never raised
            bad.append(f"probe error {type(e).__name__}")
        os.write(w, json.dumps(bad).encode())
        os._exit(0)
    os.close(w)
    data = b""
    while chunk := os.read(r, 65536):
        data += chunk
    os.close(r)
    os.waitpid(pid, 0)
    return json.loads(data or b"[]")


# ── the turn's prompt never rides a command line ──────────────────────────────────────────────────

def prompt_check(token: str) -> tuple:
    """Start a chat turn whose prompt carries a marker and watch every command line while its CLI runs.
    Returns (failures, note)."""
    admin_key = environ_of(1).get(b"ADMIN_API_TOKEN", b"").decode()
    email = "r4check-chat@example.invalid"
    code, body = http("POST", f"{ADMIN}/admin/users", {"X-Admin-API-Key": admin_key},
                      {"email": email, "name": "R4 check"})
    if code >= 300:
        code, body = http("GET", f"{ADMIN}/admin/users/email/{email}", {"X-Admin-API-Key": admin_key})
    user = json.loads(body)["id"]
    code, body = http("POST", f"{ADMIN}/admin/users/{user}/tokens?scopes=bot,tx", {"X-Admin-API-Key": admin_key})
    api_key = json.loads(body)["token"]
    marker = f"r4check-prompt-{secrets.token_hex(6)}"
    answer = {"text": b""}

    def chat():
        req = urllib.request.Request(f"{GATEWAY}/agent/chat", method="POST",
                                     data=json.dumps({"prompt": f"{marker} say hello"}).encode(),
                                     headers={"Content-Type": "application/json", "X-API-Key": api_key})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                while chunk := r.read(256):
                    answer["text"] += chunk
                    if len(answer["text"]) > 4096:
                        return
        except Exception:                            # noqa: BLE001 — the stream ending is expected
            pass

    threading.Thread(target=chat, daemon=True).start()
    failures, cli_seen, deadline = [], None, time.time() + 90
    while time.time() < deadline:
        if b"No model credentials" in answer["text"]:
            return [], "prompt check skipped: no model credentials configured, so no turn can start"
        for pid in pids():
            try:
                line = cmdline(pid)
                st = status(pid)
            except OSError:
                continue
            if marker.encode() in line:
                failures.append(f"pid {pid} ({st['name']}) carries the turn's prompt on its command line")
            if line.startswith(b"claude\0") and UID_BASE <= st["uids"][0] < SUBJECT_UID_BASE:
                cli_seen = cli_seen or time.time()
        if failures or (cli_seen and time.time() - cli_seen > 5):
            break
        time.sleep(0.05)
    for wl in json.loads(http("GET", f"{RUNTIME}/workloads", {"Authorization": f"Bearer {token}"})[1] or b"[]"):
        wid = wl.get("workloadId", "") if isinstance(wl, dict) else ""
        if wid.startswith(f"agent-{user}-"):
            call("DELETE", f"/workloads/{wid}", token)
    if not cli_seen and not failures:
        failures.append("a chat turn started no claude CLI within 90s (the prompt check did not run)")
    return failures, "the turn's prompt was on no command line while its CLI ran" if cli_seen else ""


def loose_modes() -> list:
    """Each root-only path as the filesystem holds it, for every uid at once: root's, and neither its
    group nor others may read or write it (a directory may let others pass through)."""
    out = []
    for path in ROOT_ONLY:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue
        if st.st_uid != 0 or st.st_mode & 0o066:
            out.append(f"{path} is mode {oct(st.st_mode & 0o7777)}, uid {st.st_uid}")
    return out


def secret_values() -> list:
    values = []
    for pid in (1, runtime_pid()):
        for key, value in environ_of(pid).items():
            if SECRET_NAME.search(key.decode(errors="replace")) and len(value) >= 16:
                values.append(value)
    values.append(runtime_token().encode())
    return values


def main() -> int:
    failures, notes = [], []
    token, rt = runtime_token(), runtime_pid()
    watch = Watch(rt)
    watch.start()
    mount = lambda subject: json.dumps([{"slug": "r4check", "path": f"/workspaces/{subject}",  # noqa: E731
                                         "role": "private", "write": True, "primary": True}])
    cases = [
        ("r4check-worker-num", "agent", {"VEXA_OWNER": "4242", "VEXA_MOUNTS": mount("4242")}, True),
        ("r4check-worker-named", "agent", {"VEXA_OWNER": "r4check-named", "VEXA_MOUNTS": mount("r4check-named")}, True),
        ("r4check-bot", "meeting-bot", {"VEXA_BOT_CONFIG": "{}"}, True),
        ("r4check-refused", "agent", {"VEXA_OWNER": "../r4check", "VEXA_MOUNTS": mount("r4check")}, False),
    ]
    for wid, profile, env, ok in cases:
        code = call("POST", "/workloads", token, {"workloadId": wid, "profile": profile, "env": env})
        if ok and code != 201:
            failures.append(f"{wid}: the runtime answered {code}")
        if not ok and code == 201:
            failures.append(f"{wid}: a dispatch with an unmappable subject was started")
    time.sleep(WATCH_SECONDS)
    prompt_failures, note = prompt_check(token)
    failures += prompt_failures
    if note:
        notes.append(note)
    watch.stop.set()
    watch.join()
    seen = watch.seen
    for pid, st in seen.items():
        if 0 in st["uids"] or 0 in st["gids"] or 0 in st["groups"]:
            failures.append(f"pid {pid} ({st['name']}) holds root")
        if st["nnp"] != "1":
            failures.append(f"pid {pid} ({st['name']}) can gain privileges (NoNewPrivs {st['nnp']})")
    uids = {st["uids"][0] for st in seen.values()}
    if UID_BASE + 4242 not in uids:
        failures.append("no process ran as the numeric subject's uid")
    if not any(SUBJECT_UID_BASE <= u < WORKLOAD_UID_BASE for u in uids):
        failures.append("no process ran as the named subject's uid")
    bots = [st for st in seen.values() if st["uids"][0] >= WORKLOAD_UID_BASE]
    if not bots:
        failures.append("no process ran as a workload uid (the bot)")
    for st in seen.values():
        if st["groups"]:
            failures.append(f"a child process ({st['name']}) holds supplementary groups {st['groups']}")
    if os.path.exists("/tmp/.X11-unix/X99") or os.path.exists("/tmp/.X99-lock"):
        failures.append("a shared display :99 exists")
    others = pids()
    identities = {(tuple(st["uids"]), st["gids"][0], tuple(st["groups"])) for st in seen.values()}
    for ids, gid, groups in identities:
        bot = ids[0] >= WORKLOAD_UID_BASE
        failures += [f"uid {ids[0]}: {b}" for b in probe_as(list(ids), gid, list(groups), others, bot)]
    failures += [f"root-only path open to others: {m}" for m in loose_modes()]
    if not os.path.exists("/run/vexa/key.env"):
        notes.append("self-host keys not minted on this boot (VEXA_API_KEY supplied?): key.env not checked")
    secrets_ = secret_values()
    for pid in others:
        try:
            line = cmdline(pid)
        except OSError:
            continue
        if any(s in line for s in secrets_):
            failures.append(f"pid {pid} carries a service secret on its command line")
    for wid, _profile, _env, _ok in cases:
        call("DELETE", f"/workloads/{wid}", token)
    if failures:
        for f in sorted(set(failures)):
            print(f"  ✗ child identities: {f}", file=sys.stderr)
        return 1
    print(f"  ✓ child identities: {len(seen)} spawned processes, {len(identities)} identities — none root, "
          "none can read another process's environment or root's state, none holds a group; no shared "
          "display, no VNC; refused dispatch started nothing; no secret on any command line")
    for note in notes:
        print(f"  ✓ {note}" if "skipped" not in note else f"  · {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
