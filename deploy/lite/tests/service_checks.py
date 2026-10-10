"""Run INSIDE a booted Lite container (`make -C deploy/lite test` pipes it to `docker exec -i … python3 -`):

* the admin key the services run with is none of the values admin-api refuses as published (its own
  config.v1 `forbidden_values`, read from the image);
* admin-api reads its delegation store the way it admits a worker's token: the function its
  /internal/validate awaits on the delegation module (found in validate.py, so a rename fails this
  check by name instead of passing on a stale one), run with admin-api's own environment, admits a
  probe token only while its live record exists, refuses it once a revocation key exists too, and
  refuses one never recorded (an unreachable store makes /internal/validate refuse every worker's
  token with 503).

Stdlib only; runs as root; never prints a value. Exits 1 naming what failed.
"""
import json
import re
import subprocess
import sys

CONF = "/etc/supervisor/conf.d/vexa.conf"
ADMIN_CONFIG = "/app/admin-api/src/admin_api/config.v1.json"
VALIDATE = "/app/admin-api/src/admin_api/app/validate.py"
#: Run in admin-api's venv with its REDIS_URL: the delegation module alone (not the app), its own
#: key prefixes, a probe token never issued, then held live, then revoked; every key removed after.
PROBE = """
import asyncio, importlib.util, os, secrets
import redis.asyncio as aioredis
spec = importlib.util.spec_from_file_location("rev", "/app/admin-api/src/admin_api/app/delegation_revocation.py")
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
admit = getattr(r, "@FUNC@")
jti = "r4check-" + secrets.token_hex(8)
async def main():
    c = aioredis.from_url(os.environ["REDIS_URL"])
    try:
        never = await admit(jti)
        await c.set(r.LIVE_PREFIX + jti, "1", ex=60)
        live = await admit(jti)
        await c.set(r.REVOKED_PREFIX + jti, "1", ex=60)
        revoked = await admit(jti)
    finally:
        await c.delete(r.LIVE_PREFIX + jti, r.REVOKED_PREFIX + jti)
    print(f"never={never} live={live} revoked={revoked}")
asyncio.run(main())
"""


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
    # The admission function is whatever /internal/validate awaits on the delegation module.
    validate_src = open(VALIDATE, encoding="utf-8").read()
    called = sorted(set(re.findall(r"await revocation\.([a-z_]+)\(", validate_src)))
    if len(called) != 1:
        failures.append(f"admin-api's /internal/validate awaits {called or 'nothing'} on its delegation "
                        "module; this check knows one admission function and must be updated")
    else:
        probe = PROBE.replace("@FUNC@", called[0])
        run = subprocess.run(["/opt/venvs/admin/bin/python", "-c", probe], capture_output=True, text=True,
                             timeout=60, cwd="/app/admin-api",
                             env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "/app/admin-api/src",
                                  "REDIS_URL": admin_env.get("REDIS_URL", "")})
        if run.returncode != 0 or run.stdout.strip() != "never=False live=True revoked=False":
            failures.append(f"admin-api's {called[0]} does not read its delegation store as it admits a "
                            f"token ({(run.stderr.strip().splitlines() or [run.stdout.strip() or 'no output'])[-1][:160]})")
    if failures:
        for f in failures:
            print(f"  ✗ services: {f}", file=sys.stderr)
        return 1
    print(f"  ✓ services: the admin key is none of the {len(published)} published values; "
          "admin-api admits a worker token only while its live record exists and no revocation does")
    return 0


if __name__ == "__main__":
    sys.exit(main())
