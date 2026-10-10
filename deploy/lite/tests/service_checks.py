"""Run INSIDE a booted Lite container (`make -C deploy/lite test` pipes it to `docker exec -i … python3 -`):

* the admin key the services run with is none of the values admin-api refuses as published (its own
  config.v1 `forbidden_values`, read from the image);
* admin-api reaches its delegation revocation store: its own `is_revoked`, run with admin-api's own
  environment, answers instead of raising (an unreachable store makes /internal/validate refuse every
  worker's token with 503).

Stdlib only; runs as root; never prints a value. Exits 1 naming what failed.
"""
import json
import re
import subprocess
import sys

CONF = "/etc/supervisor/conf.d/vexa.conf"
ADMIN_CONFIG = "/app/admin-api/src/admin_api/config.v1.json"


def program_pid(name: str) -> int:
    out = subprocess.run(["supervisorctl", "-c", CONF, "status"], capture_output=True, text=True).stdout
    return int(re.search(rf"^\S*{re.escape(name)}\s+RUNNING\s+pid (\d+)", out, flags=re.M).group(1))


def environ_of(pid: int) -> dict:
    with open(f"/proc/{pid}/environ", "rb") as f:
        return dict(e.decode(errors="replace").split("=", 1) for e in f.read().split(b"\0") if b"=" in e)


def main() -> int:
    failures = []
    admin_env = environ_of(program_pid("admin-api"))
    keys = json.load(open(ADMIN_CONFIG, encoding="utf-8"))["keys"]
    published = next(k.get("forbidden_values", []) for k in keys if k["key"] == "ADMIN_API_TOKEN")
    key = admin_env.get("ADMIN_API_TOKEN", "")
    if not published:
        failures.append("admin-api declares no published admin keys to check against")
    if not key or key in published:
        failures.append("the admin key admin-api runs with is unset or published in the Vexa repository")
    if environ_of(program_pid("meeting-api")).get("ADMIN_TOKEN") != key:
        failures.append("meeting-api runs with a different admin key from admin-api")
    probe = ("import asyncio, importlib.util\n"          # the module alone, not the app it serves
             "spec = importlib.util.spec_from_file_location('rev', "
             "'/app/admin-api/src/admin_api/app/delegation_revocation.py')\n"
             "r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)\n"
             "print(asyncio.run(r.is_revoked('r4check-never-issued')))\n")
    run = subprocess.run(["/opt/venvs/admin/bin/python", "-c", probe], capture_output=True, text=True, timeout=30,
                         cwd="/app/admin-api",
                         env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "/app/admin-api/src",
                              "REDIS_URL": admin_env.get("REDIS_URL", "")})
    if run.returncode != 0 or run.stdout.strip() != "False":
        failures.append(f"admin-api cannot read its delegation revocation store "
                        f"({(run.stderr.strip().splitlines() or ['no output'])[-1][:120]})")
    if failures:
        for f in failures:
            print(f"  ✗ services: {f}", file=sys.stderr)
        return 1
    print(f"  ✓ services: the admin key is none of the {len(published)} published values; "
          "admin-api reads its delegation revocation store")
    return 0


if __name__ == "__main__":
    sys.exit(main())
