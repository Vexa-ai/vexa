"""The session store's own credentials on compose (ADR-0048; offline — no stack, no docker).

agent-api reaches the session store as its OWN Postgres role and with its OWN S3 key, scoped to its
own bucket: never the instance's admin user, never the storage root pair, never the recordings bucket.
The migrations path (agent-sessions-init) alone holds the admin credential; storage-init makes the
bucket and the key's scope; mint-dev-env.sh mints the three secrets.
"""
from __future__ import annotations

import re
import subprocess

from workspace_store_wiring_test import ROOT, _service

MINT = ROOT / "deploy" / "compose" / "mint-dev-env.sh"


def _env(block: str, key: str):
    got = re.findall(rf"^\s+- {key}=(.*)$", block, flags=re.M)
    return got[0].strip() if got else None


def _depends(block: str) -> dict:
    """``{service: condition}`` under the block's depends_on."""
    m = re.search(r"^    depends_on:\n((?:      .*\n)+)", block + "\n", flags=re.M)
    return dict(re.findall(r"^      ([a-z0-9-]+):\n        condition: (\S+)", m.group(1), flags=re.M)) if m else {}


def test_agent_api_holds_no_admin_database_or_storage_root_credential():
    api = _service("agent-api")
    for name in ("DB_USER", "DB_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        assert _env(api, name) is None, name
    assert _env(api, "VEXA_SESSIONS_DB_USER") == "${VEXA_SESSIONS_DB_USER:-vexa_agent_sessions}"
    assert _env(api, "VEXA_SESSIONS_DB_PASSWORD") == "${VEXA_SESSIONS_DB_PASSWORD:-}"
    assert _env(api, "VEXA_SESSIONS_S3_ACCESS_KEY") == "${VEXA_SESSIONS_S3_ACCESS_KEY:-}"
    assert "MINIO" not in (_env(api, "VEXA_SESSIONS_S3_SECRET_KEY") or "")
    assert _env(api, "VEXA_SESSIONS_S3_BUCKET") == "${VEXA_SESSIONS_S3_BUCKET:-vexa-sessions}"


def test_the_migrations_path_holds_the_admin_credential_and_agent_api_waits_for_it():
    init = _service("agent-sessions-init")
    assert _env(init, "DB_PASSWORD") == "${DB_PASSWORD:-}"
    assert "session_schema" in init and '"init"' in init
    deps = _depends(_service("agent-api"))
    assert deps["agent-sessions-init"] == "service_completed_successfully"
    assert deps["storage-init"] == "service_completed_successfully"


def test_storage_init_is_handed_the_sessions_key_to_scope():
    si = _service("storage-init")
    assert _env(si, "VEXA_SESSIONS_S3_ACCESS_KEY") == "${VEXA_SESSIONS_S3_ACCESS_KEY:-}"
    assert _env(si, "VEXA_SESSIONS_S3_BUCKET") == "${VEXA_SESSIONS_S3_BUCKET:-vexa-sessions}"


def test_mint_dev_env_mints_the_session_store_secrets_once(tmp_path):
    env = tmp_path / ".env"
    env.write_text("")
    subprocess.run(["bash", str(MINT), str(env)], check=True, capture_output=True, text=True)
    vals = dict(re.findall(r"^(VEXA_SESSIONS_[A-Z0-9_]+)=(.*)$", env.read_text(), flags=re.M))
    assert vals["VEXA_SESSIONS_S3_ACCESS_KEY"].startswith("vexa-sessions-")
    assert len(vals["VEXA_SESSIONS_S3_SECRET_KEY"]) == 64 and len(vals["VEXA_SESSIONS_DB_PASSWORD"]) == 64
    out = subprocess.run(["bash", str(MINT), str(env)], check=True, capture_output=True, text=True).stdout
    again = dict(re.findall(r"^(VEXA_SESSIONS_[A-Z0-9_]+)=(.*)$", env.read_text(), flags=re.M))
    assert again == vals
    for v in vals.values():
        assert v not in out
