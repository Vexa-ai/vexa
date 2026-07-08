"""calendar_sync — ICS feed → planned meetings (see README.md).

Public surface: ``parse_ics`` / ``sync_user`` (pure logic) + the production I/O adapters
``fetch_ics`` / ``fetch_configs``. The entrypoint (``meeting_api.__main__``) wires them into the
standard background poll loop.
"""
from .adapters import fetch_configs, fetch_ics
from .service import parse_ics, sync_user

__all__ = ["parse_ics", "sync_user", "fetch_ics", "fetch_configs"]
