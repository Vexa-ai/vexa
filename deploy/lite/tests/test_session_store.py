"""Lite: the session store's own database role and its own bucket (ADR-0048; offline).

agent-api's environment carries the store's own role and scoped key — never the instance's admin
database user or the storage root pair — and the entrypoint makes both with the credentials it
already holds, before supervisord starts agent-api. No directory store.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LITE = ROOT / "deploy" / "lite"


def _agent_api_env() -> str:
    text = (LITE / "supervisord.conf").read_text()
    block = text[text.index("[program:agent-api]"):]
    return re.search(r"^environment=(.*)$", block, flags=re.M).group(1)


def test_agent_api_holds_neither_the_admin_database_user_nor_the_storage_root():
    env = _agent_api_env()
    for name in ("DB_USER", "DB_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"):
        assert not re.search(rf'(^|,){name}=', env), name
        assert f"ENV_{name})" not in env.replace("ENV_MINIO_SECURE", ""), name
    for name in ("VEXA_SESSIONS_DB_USER", "VEXA_SESSIONS_DB_PASSWORD", "VEXA_SESSIONS_S3_ACCESS_KEY",
                 "VEXA_SESSIONS_S3_SECRET_KEY", "VEXA_SESSIONS_S3_BUCKET"):
        assert f'{name}="%(ENV_{name})s"' in env
    assert "STORE_DIR" not in env


def test_the_entrypoint_makes_the_role_and_bucket_before_supervisord():
    text = (LITE / "entrypoint.sh").read_text()
    init = text.index("control_plane.session_schema init")
    bucket = text.index("storage_init.py --sessions-only")
    assert init < text.index('exec "$@"') and bucket < text.index('exec "$@"')
    assert 'VEXA_SESSIONS_S3_BUCKET="${VEXA_SESSIONS_S3_BUCKET:-vexa-sessions}"' in text
    assert "COPY deploy/storage/storage_init.py /app/storage-tools/storage_init.py" in (LITE / "Dockerfile.lite").read_text()
