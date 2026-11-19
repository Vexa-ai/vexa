"""Lite's admin key is never a published value.

admin-api, meeting-api and flows refuse, at boot, every value this repository ever published for the
admin key. Lite would then not boot on an install that kept one, so: `make up` replaces an .env
ADMIN_TOKEN that is unset or published with a minted key (as it does DB_PASSWORD and the storage
pair), the entrypoint refuses a given published key, the services' list is the one both use (fact
admin-token-placeholders), and admin-api is given the Redis its delegation revocation store lives in.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LITE = ROOT / "deploy" / "lite"
MAKEFILE = (LITE / "Makefile").read_text()
ENTRYPOINT = (LITE / "entrypoint.sh").read_text()


def _services_list() -> set[str]:
    keys = json.loads((ROOT / "core/identity/services/admin-api/src/admin_api/config.v1.json").read_text())["keys"]
    return set(next(k["forbidden_values"] for k in keys if k["key"] == "ADMIN_API_TOKEN"))


def _split(raw: str) -> set[str]:
    return {t.strip('"') for t in raw.split("|")} - {""}


def test_make_up_replaces_an_unset_or_published_key():
    listed = _split(re.search(r"^ADMIN_PUBLISHED := (.*)$", MAKEFILE, flags=re.M).group(1))
    assert listed == _services_list()
    assert re.search(r'^ADMIN_PUBLISHED := ""\|', MAKEFILE, flags=re.M)           # unset is replaced too
    assert 'case "$$ADMIN_TK" in $(ADMIN_PUBLISHED))' in MAKEFILE
    assert 'sed -i.bak "s|^ADMIN_TOKEN=.*|ADMIN_TOKEN=$$ADMIN_TK|"' in MAKEFILE
    assert '-e ADMIN_API_TOKEN="$$ADMIN_TK" -e ADMIN_TOKEN="$$ADMIN_TK"' in MAKEFILE


def _block() -> str:
    return re.search(r'^case "\$ADMIN_API_TOKEN" in\n.*?^esac\n', ENTRYPOINT, flags=re.S | re.M).group(0)


def _start(value: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", _block() + "echo started"], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "ADMIN_API_TOKEN": value})


def test_the_entrypoint_refuses_a_published_key():
    assert _split(re.search(r'case "\$ADMIN_API_TOKEN" in\n\s*([^)\n]*)\)', ENTRYPOINT).group(1)) == _services_list()
    for value in sorted(_services_list()):
        r = _start(value)
        assert r.returncode == 1 and "started" not in r.stdout, value
        assert "published in the Vexa repository" in r.stderr
    assert _start("a" * 64).stdout.strip() == "started"


def test_admin_api_is_given_the_redis_its_revocation_store_lives_in():
    supervisord = (LITE / "supervisord.conf").read_text()
    admin = re.search(r"^\[program:admin-api\]\n(.*?)\n\n", supervisord, flags=re.S | re.M).group(1)
    (env,) = re.findall(r"^environment=(.*)$", admin, flags=re.M)
    assert 'REDIS_URL="%(ENV_REDIS_URL)s"' in env


def test_the_live_checks_run_in_make_test():
    assert 'tests/service_checks.py" || FAIL=1' in MAKEFILE
