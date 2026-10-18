"""Run INSIDE a booted Lite container (`make -C deploy/lite test` pipes it to `docker exec -i … python3 -`):
read every process's environment and check that the runtime caller credential is held by the runtime,
agent-api and meeting-api — and by nothing else: not supervisord, not any other program, not a bot or
worker the runtime spawned. Exits 1 with the offending programs named; never prints a value.

Stdlib only; reads /proc as root (the container's own processes). A process the kernel will not show
(a worker that dropped to its tenant uid is not dumpable) is skipped: the runtime builds every child's
environment from scratch, which core/runtime's tests prove offline.
"""
import os
import re
import subprocess
import sys
import time

KEY = b"RUNTIME_API_TOKEN="
HOLDERS = {"runtime", "agent-api", "meeting-api"}
CONF = "/etc/supervisor/conf.d/vexa.conf"


def programs() -> dict[int, str]:
    """pid → program name for every RUNNING supervised program (group prefix dropped)."""
    out = subprocess.run(["supervisorctl", "-c", CONF, "status"], capture_output=True, text=True).stdout
    return {int(pid): name.split(":")[-1]
            for name, pid in re.findall(r"^(\S+)\s+RUNNING\s+pid (\d+)", out, flags=re.M)}


def parent(pid: int) -> int:
    with open(f"/proc/{pid}/stat", "rb") as f:
        return int(f.read().rsplit(b")", 1)[1].split()[1])


def holds(pid: int) -> bool:
    with open(f"/proc/{pid}/environ", "rb") as f:
        return any(entry.startswith(KEY) for entry in f.read().split(b"\0"))


def main() -> int:
    deadline = time.time() + 90
    progs = programs()
    while not HOLDERS <= set(progs.values()) and time.time() < deadline:
        time.sleep(3)
        progs = programs()
    missing = HOLDERS - set(progs.values())
    if missing:
        print(f"  ✗ program environments: {sorted(missing)} not running", file=sys.stderr)
        return 1

    held_by, offenders = set(), set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            if not holds(pid):
                continue
            node, owner = pid, None
            while node > 1:
                if node in progs:
                    owner = progs[node]
                    break
                node = parent(node)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue                                   # exited while we looked
        # A holder program's own process holds it; so may agent-api's and meeting-api's helpers. A
        # child of the runtime is a bot or a worker and must not.
        if owner in HOLDERS and (pid in progs or owner != "runtime"):
            held_by.add(owner)
        else:
            offenders.add(owner or f"pid {pid} (no supervised program)")
    if offenders or held_by != HOLDERS:
        print(f"  ✗ RUNTIME_API_TOKEN held by {sorted(offenders)}; holders seen {sorted(held_by)}",
              file=sys.stderr)
        return 1
    print("  ✓ RUNTIME_API_TOKEN: held by the runtime, agent-api and meeting-api only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
