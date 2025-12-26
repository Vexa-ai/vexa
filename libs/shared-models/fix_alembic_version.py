#!/usr/bin/env python3
"""
Safely repair Alembic state when the DB contains a revision that no longer exists
in the codebase (e.g. "Can't locate revision identified by ...").

Design goals:
- Never drop schemas or user tables.
- Only touch the `alembic_version` table (create it if missing when requested).
- If the current DB revision is not present in the migration scripts, update it to head.
"""

from __future__ import annotations

import argparse
import os
from typing import Optional

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text


def get_script_head_revision(alembic_ini_path: str) -> str:
    cfg = Config(alembic_ini_path)
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    if not heads:
        raise RuntimeError("No Alembic heads found in script directory")
    if len(heads) > 1:
        raise RuntimeError(f"Multiple Alembic heads found: {heads}")
    return heads[0]


def revision_exists_in_scripts(alembic_ini_path: str, revision: str) -> bool:
    cfg = Config(alembic_ini_path)
    script = ScriptDirectory.from_config(cfg)
    try:
        return script.get_revision(revision) is not None
    except Exception:
        return False


def get_database_url_sync() -> str:
    url = os.environ.get("DATABASE_URL_SYNC")
    if url:
        return url

    # Reasonable fallback for local docker-compose; env.py in shared-models sets DATABASE_URL_SYNC,
    # but we keep this to avoid crashing if it's missing.
    db_host = os.environ.get("DB_HOST", "postgres")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME", "vexa")
    db_user = os.environ.get("DB_USER", "postgres")
    db_password = os.environ.get("DB_PASSWORD", "postgres")
    db_ssl_mode = os.environ.get("DB_SSL_MODE", "prefer")
    ssl_params = f"?sslmode={db_ssl_mode}" if db_ssl_mode else ""
    return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}{ssl_params}"


def alembic_version_exists(conn) -> bool:
    # Portable check using information_schema
    res = conn.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version' LIMIT 1;")
    ).scalar()
    return bool(res)


def read_current_revision(conn) -> Optional[str]:
    try:
        return conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1;")).scalar()
    except Exception:
        return None


def ensure_table_and_set_revision(conn, revision: str) -> None:
    # Minimal, non-destructive: ensure table exists then ensure a single row with revision.
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS alembic_version ("
            "version_num VARCHAR(32) NOT NULL,"
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
            ");"
        )
    )

    # If table has a row, update it; otherwise insert.
    updated = conn.execute(
        text("UPDATE alembic_version SET version_num = :rev;"), {"rev": revision}
    ).rowcount
    if not updated:
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:rev);"), {"rev": revision})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--alembic-ini",
        default="/app/alembic.ini",
        help="Path to alembic.ini inside the container (default: /app/alembic.ini)",
    )
    parser.add_argument(
        "--repair-stale",
        action="store_true",
        help="If alembic_version exists and current revision is not present in scripts, set it to head.",
    )
    parser.add_argument(
        "--create-if-missing",
        action="store_true",
        help="If alembic_version table is missing, create it and set it to head.",
    )
    args = parser.parse_args()

    if not args.repair_stale and not args.create_if_missing:
        raise SystemExit("No action requested. Use --repair-stale and/or --create-if-missing.")

    head = get_script_head_revision(args.alembic_ini)
    db_url = get_database_url_sync()
    engine = create_engine(db_url)

    with engine.begin() as conn:
        exists = alembic_version_exists(conn)
        if not exists:
            if args.create_if_missing:
                ensure_table_and_set_revision(conn, head)
                print(f"alembic_version missing -> created + set to head {head}")
            else:
                print("alembic_version missing -> nothing to do (create not requested)")
            return 0

        current = read_current_revision(conn)
        if not current:
            if args.repair_stale:
                ensure_table_and_set_revision(conn, head)
                print(f"alembic_version empty/unknown -> set to head {head}")
            return 0

        if args.repair_stale and not revision_exists_in_scripts(args.alembic_ini, current):
            ensure_table_and_set_revision(conn, head)
            print(f"alembic_version stale ({current}) -> updated to head {head}")
        else:
            # Nothing to do.
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())










