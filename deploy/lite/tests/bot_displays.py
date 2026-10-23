"""Run INSIDE a booted Lite container while two or more meeting bots run (tests/concurrent-bots.sh
pipes it in during its window): each bot has its own X display, and no bot can capture another's.

* every running bot (its node worker, a workload uid) has exactly one X socket of its own and one
  cookie file in its HOME; no two bots share a display; each display is served by an Xvfb running as that bot's uid in that
  bot's process group, and both of its sockets (the abstract one and /tmp/.X11-unix/X<n>) are
  answered by that Xvfb; the cookie file is the bot's (0600, in a 0700 directory). No X server runs
  as root, there is no :99, and no VNC or noVNC process runs;
* as each bot's own identity: its display opens with its cookie and ffmpeg x11grab captures a frame
  of it; every other bot's cookie file is unreadable, and every other bot's display refuses a
  connection with no cookie and with this bot's cookie, and gives ffmpeg x11grab nothing;
* as an identity no process holds (an agent worker's kind: no group, no cookie): no display opens.

Stdlib and the image's ffmpeg; runs as root; never prints a cookie. Exits 1 naming what failed.
"""
import json
import os
import socket
import struct
import subprocess
import sys

WORKLOAD_UID_BASE = 1_500_000_000
SOCKET_DIR = "/tmp/.X11-unix"
HOMES = "/var/lib/vexa-runtime/homes"
STRANGER = (1_499_999_999, 1_499_999_999)        # a uid and gid no process holds


def status(pid: int) -> dict:
    out = {}
    with open(f"/proc/{pid}/status", encoding="utf-8") as f:
        for line in f:
            key, _, value = line.partition(":")
            out[key] = value.split()
    return {"name": out["Name"][0], "uids": [int(x) for x in out["Uid"]],
            "gids": [int(x) for x in out["Gid"]], "groups": [int(x) for x in out.get("Groups", [])]}


def cmdline(pid: int) -> bytes:
    with open(f"/proc/{pid}/cmdline", "rb") as f:
        return f.read()


def pgrp(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return int(f.read().rsplit(")", 1)[1].split()[2])
    except OSError:
        return -1


def pids() -> list:
    return [int(p) for p in os.listdir("/proc") if p.isdigit()]


def x_cookie(data: bytes):
    i = 0
    while i + 2 <= len(data):
        i += 2
        fields = []
        for _ in range(4):
            (n,) = struct.unpack(">H", data[i:i + 2])
            fields.append(data[i + 2:i + 2 + n])
            i += 2 + n
        if fields[2] == b"MIT-MAGIC-COOKIE-1":
            return fields[3]
    return None


def x_open(address: str, cookie) -> int:
    """The X server's answer to a connection setup: 1 opened, 0 refused, -1 nothing reachable."""
    name, data = (b"MIT-MAGIC-COOKIE-1", cookie) if cookie else (b"", b"")
    pad = lambda b: b + b"\0" * (-len(b) % 4)       # noqa: E731
    req = b"l\0" + struct.pack("<HHHH", 11, 0, len(name), len(data)) + b"\0\0" + pad(name) + pad(data)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(address)
            s.sendall(req)
            return 1 if s.recv(1) == b"\x01" else 0
    except OSError:
        return -1


def peer(address: str):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(address)
            pid, uid, _ = struct.unpack("3i", s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            return pid, uid
    except OSError:
        return None


def grab(display: str, xauthority: str) -> bool:
    """Whether ffmpeg's x11grab captures one frame of this display with this cookie file."""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "XAUTHORITY": xauthority, "HOME": "/nonexistent"}
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "x11grab", "-video_size", "64x64",
                        "-i", display, "-frames:v", "1", "-f", "null", "-"],
                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    return r.returncode == 0


def addresses(display: str) -> list:
    path = f"{SOCKET_DIR}/X{display.lstrip(':')}"
    return ["\0" + path, path]


def probe_as(uid: int, gid: int, me: dict, others: list) -> list:
    """Fork, become this identity, and try to reach each display. Returns failures."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        bad = []
        try:
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            own = None
            if me:
                own = x_cookie(open(me["xauthority"], "rb").read())
                if not all(x_open(a, own) == 1 for a in addresses(me["display"])):
                    bad.append(f"could not open its own display {me['display']} with its cookie")
                if not grab(me["display"], me["xauthority"]):
                    bad.append(f"could not capture its own display {me['display']}")
            for other in others:
                try:
                    open(other["xauthority"], "rb").read(1)
                    bad.append(f"read the cookie of display {other['display']}")
                except (PermissionError, FileNotFoundError):
                    pass
                for address in addresses(other["display"]):
                    kind = "abstract" if address.startswith("\0") else "path"
                    if x_open(address, None) == 1:
                        bad.append(f"opened display {other['display']} ({kind}) with no cookie")
                    if own and x_open(address, own) == 1:
                        bad.append(f"opened display {other['display']} ({kind}) with its own cookie")
                if grab(other["display"], me["xauthority"] if me else "/nonexistent"):
                    bad.append(f"captured display {other['display']}")
        except Exception as e:                       # noqa: BLE001 — reported, never raised
            bad.append(f"probe error {type(e).__name__}: {e}")
        os.write(w, json.dumps(bad).encode())
        os._exit(0)
    os.close(w)
    data = b""
    while chunk := os.read(r, 65536):
        data += chunk
    os.close(r)
    os.waitpid(pid, 0)
    return json.loads(data or b"[]")


def main() -> int:
    failures = []
    procs = {}
    for pid in pids():
        try:
            procs[pid] = (status(pid), cmdline(pid))
        except OSError:
            continue
    # Found from what root may read without ptrace (no process's environment: root here holds no
    # CAP_SYS_PTRACE): the bot's HOME is the runtime's <uid>.<random> directory and its cookie is in
    # it; its X server is the Xvfb of its uid in its process group, and its display is the socket
    # that Xvfb answers on (a uid used by an earlier bot may still own a dead socket).
    bots = []
    for pid, (st, line) in procs.items():
        if st["uids"][0] >= WORKLOAD_UID_BASE and line.startswith(b"node\0dist/index.js"):
            uid, group = st["uids"][0], pgrp(pid)
            homes = [h for h in os.listdir(HOMES) if h.split(".")[0] == str(uid)]
            xvfb = [p for p, (s2, _l) in procs.items()
                    if s2["name"] == "Xvfb" and s2["uids"][0] == uid and pgrp(p) == group]
            sockets = [n for n in os.listdir(SOCKET_DIR) if n.startswith("X") and n[1:].isdigit()
                       and os.lstat(f"{SOCKET_DIR}/{n}").st_uid == uid
                       and len(xvfb) == 1 and peer(f"{SOCKET_DIR}/{n}") == (xvfb[0], uid)]
            bots.append({"pid": pid, "uid": uid, "gid": st["gids"][0], "pgrp": group, "xvfb": xvfb,
                         "display": ":" + sockets[0][1:] if len(sockets) == 1 else "",
                         "xauthority": f"{HOMES}/{homes[0]}/x11/Xauthority" if len(homes) == 1 else ""})
    if len(bots) < 2:
        print(f"  ✗ bot displays: {len(bots)} bot(s) running; two are needed to check isolation", file=sys.stderr)
        return 1
    if len({b["display"] for b in bots}) != len(bots) or not all(b["display"] for b in bots):
        failures.append(f"bots do not each have their own display: {sorted(b['display'] for b in bots)}")
    for pid, (st, line) in procs.items():
        if st["name"] == "Xvfb" and 0 in st["uids"]:
            failures.append(f"an X server runs as root (pid {pid})")
        if st["name"] in ("x11vnc", "websockify") or b"websockify" in line:
            failures.append(f"a VNC process runs ({st['name']}, pid {pid})")
    if os.path.exists(f"{SOCKET_DIR}/X99"):
        failures.append("a shared display :99 exists")
    sd = os.lstat(SOCKET_DIR)
    if sd.st_uid != 0 or not sd.st_mode & 0o1000:
        failures.append(f"{SOCKET_DIR} is not root's and sticky")
    for b in bots:
        xvfb = b["xvfb"]
        if len(xvfb) != 1:
            failures.append(f"bot {b['uid']}: {len(xvfb)} X servers of its own in its process group")
            continue
        for address in addresses(b["display"]):
            if peer(address) != (xvfb[0], b["uid"]):
                kind = "abstract" if address.startswith("\0") else "path"
                failures.append(f"bot {b['uid']}: the {kind} socket of {b['display']} is not its Xvfb's")
        try:
            ck = os.lstat(b["xauthority"])
            home = os.lstat(os.path.dirname(b["xauthority"]))
            if ck.st_uid != b["uid"] or ck.st_mode & 0o077 or home.st_uid != b["uid"] or home.st_mode & 0o077:
                failures.append(f"bot {b['uid']}: its cookie or its directory is open to others")
        except FileNotFoundError:
            failures.append(f"bot {b['uid']}: no cookie file at {b['xauthority']}")
    for b in bots:
        others = [o for o in bots if o is not b]
        failures += [f"bot {b['uid']}: {x}" for x in probe_as(b["uid"], b["gid"], b, others)]
    failures += [f"uid {STRANGER[0]}: {x}" for x in probe_as(*STRANGER, None, bots)]
    if failures:
        for f in sorted(set(failures)):
            print(f"  ✗ bot displays: {f}", file=sys.stderr)
        return 1
    print(f"  ✓ bot displays: {len(bots)} bots, each on its own display ({', '.join(sorted(b['display'] for b in bots))}) "
          "served by its own Xvfb as its own uid; each captures its own screen and no other; "
          "a uid with no cookie opens none; no root X server, no :99, no VNC")
    return 0


if __name__ == "__main__":
    sys.exit(main())
