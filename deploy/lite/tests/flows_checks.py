"""Run INSIDE a booted Lite container (`make -C deploy/lite test` pipes it to `docker exec -i … python3 -`):
Flows is part of Lite (ADR-0052 §3: booked bots start from its clock, so every profile runs it).

* flows-api and flows-worker are RUNNING under supervisord and stay up (not a restart loop);
* flows-api answers /health on 127.0.0.1:8200, and `GET /flows` with the operator key the entrypoint
  keeps (read from flows-api's own environment), which reads its flow rows from Postgres;
* the gateway's service door (flows' only way into the other domains) answers on 127.0.0.1:8002.

Stdlib only; runs as root; never prints a value. Exits 1 naming what failed.
"""
import re
import subprocess
import sys
import time
import urllib.request

CONF = "/etc/supervisor/conf.d/vexa.conf"
PROGRAMS = ("flows-api", "flows-worker")
STEADY_S = 20          # a program up this long past its startsecs is not crash-looping


def status() -> dict:
    out = subprocess.run(["supervisorctl", "-c", CONF, "status"], capture_output=True, text=True).stdout
    return {name.split(":")[-1]: (state, rest) for name, state, rest in
            re.findall(r"^(\S+)\s+(\S+)\s*(.*)$", out, flags=re.M)}


def uptime_s(rest: str) -> int:
    m = re.search(r"uptime (?:(\d+) days?, )?(\d+):(\d+):(\d+)", rest)
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def environ_of(pid: int) -> dict:
    with open(f"/proc/{pid}/environ", "rb") as f:
        return dict(e.decode(errors="replace").split("=", 1) for e in f.read().split(b"\0") if b"=" in e)


def get(url: str, headers: "dict | None" = None) -> int:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0


def main() -> int:
    failures = []
    deadline = time.time() + 150
    st = status()
    while time.time() < deadline and not all(
            st.get(p, ("", ""))[0] == "RUNNING" and uptime_s(st[p][1]) >= STEADY_S for p in PROGRAMS):
        time.sleep(3)
        st = status()
    for p in PROGRAMS:
        state, rest = st.get(p, ("MISSING", ""))
        if state != "RUNNING" or uptime_s(rest) < STEADY_S:
            failures.append(f"{p} is {state} ({rest.strip() or 'no detail'})")
    if get("http://127.0.0.1:8200/health") != 200:
        failures.append("flows-api /health did not answer 200")
    pid = re.search(r"pid (\d+)", st.get("flows-api", ("", ""))[1] or "")
    key = environ_of(int(pid.group(1))).get("VEXA_FLOWS_API_KEY", "") if pid else ""
    if not key:
        failures.append("flows-api holds no VEXA_FLOWS_API_KEY")
    else:
        code = get("http://127.0.0.1:8200/flows", {"X-Flows-Operator-Key": key})
        if code != 200:
            failures.append(f"flows-api GET /flows with the operator key answered {code} (its database?)")
    if get("http://127.0.0.1:8002/health") != 200:
        failures.append("the gateway's service door (:8002) did not answer /health")
    if failures:
        for f in failures:
            print(f"  ✗ flows: {f}", file=sys.stderr)
        return 1
    print("  ✓ flows: flows-api and flows-worker running, flows-api reads its tables, the service door answers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
