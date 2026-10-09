"""Run INSIDE a booted Lite container (`make -C deploy/lite test` pipes it to `docker exec -i … python3 -`):
start children through the runtime the way agent-api and meeting-api do, and check what each one is.

* an agent worker for a numeric subject, one for a named subject, a meeting bot: every process the
  runtime starts (and everything those start) runs with no uid or gid 0 anywhere, no_new_privs set; a
  worker as its subject's uid, a bot as a uid of its own with only the PulseAudio group;
* a dispatch whose subject cannot be mapped is refused, and starts nothing;
* as each identity a child ran with: no other process's environment is readable, nor root's state
  (the rendered supervisor config, Valkey's config and data, the signing key, the workload logs), and
  the browser install and the workspace store are not writable;
* no process in the container carries a service secret on its command line.

Stdlib only; runs as root; never prints a value. Exits 1 naming what failed.
"""
import grp
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

RUNTIME = "http://127.0.0.1:8090"
RENDERED = "/run/vexa/supervisord.conf"
CONF = "/etc/supervisor/conf.d/vexa.conf"
UID_BASE, SUBJECT_UID_BASE, WORKLOAD_UID_BASE = 100000, 1_000_000_000, 1_500_000_000
ROOT_ONLY = ["/run/vexa/supervisord.conf", "/run/vexa/valkey.conf", "/var/lib/redis",
             "/var/lib/vexa/state/identity/signing-key.pem", "/var/lib/vexa/state/nextauth-secret",
             "/tmp/vexa-workloads"]
NOT_WRITABLE = ["/ms-playwright", "/workspaces", "/app", "/usr/local/bin"]
SECRET_NAME = re.compile(r"SECRET|TOKEN|PASSWORD|_KEY$")
WATCH_SECONDS = 20


def runtime_token() -> str:
    text = open(RENDERED, encoding="utf-8").read()
    return re.search(r'RUNTIME_API_TOKEN="([^"]+)"', text).group(1)


def call(method: str, path: str, token: str, body=None) -> int:
    req = urllib.request.Request(RUNTIME + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


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


def descendants(root: int) -> list[int]:
    parents = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                parents[int(entry)] = parent(int(entry))
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
    """Records every descendant of the runtime, as often as it can, for as long as it runs."""

    def __init__(self, root: int) -> None:
        super().__init__(daemon=True)
        self.root, self.seen, self.stop = root, {}, threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            for pid in descendants(self.root):
                if pid not in self.seen:
                    try:
                        self.seen[pid] = status(pid)
                    except OSError:
                        pass
            time.sleep(0.01)


def probe_as(uids: list, gid: int, groups: list, others: list) -> list:
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
                except (PermissionError, FileNotFoundError, OSError):
                    pass
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


def secret_values() -> list:
    values = []
    for path in ("/proc/1/environ", f"/proc/{runtime_pid()}/environ"):
        with open(path, "rb") as f:
            for entry in f.read().split(b"\0"):
                key, _, value = entry.partition(b"=")
                if SECRET_NAME.search(key.decode(errors="replace")) and len(value) >= 16:
                    values.append(value)
    values.append(runtime_token().encode())
    return values


def main() -> int:
    failures = []
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
    pulse = grp.getgrnam("pulse-access").gr_gid
    for st in bots:
        if st["groups"] != [pulse]:
            failures.append(f"a bot process ({st['name']}) has groups other than pulse-access")
    others = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    identities = {(tuple(st["uids"]), st["gids"][0], tuple(st["groups"])) for st in seen.values()}
    for ids, gid, groups in identities:
        failures += [f"uid {ids[0]}: {b}" for b in probe_as(list(ids), gid, list(groups), others)]
    secrets = secret_values()
    for pid in others:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read()
        except OSError:
            continue
        if any(s in cmdline for s in secrets):
            failures.append(f"pid {pid} carries a service secret on its command line")
    for wid, _profile, _env, _ok in cases:
        call("DELETE", f"/workloads/{wid}", token)
    if failures:
        for f in sorted(set(failures)):
            print(f"  ✗ child identities: {f}", file=sys.stderr)
        return 1
    print(f"  ✓ child identities: {len(seen)} spawned processes, {len(identities)} identities — none root, "
          "none can read another process's environment or root's state; refused dispatch started nothing; "
          "no secret on any command line")
    return 0


if __name__ == "__main__":
    sys.exit(main())
