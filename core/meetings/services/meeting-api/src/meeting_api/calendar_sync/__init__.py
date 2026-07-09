"""calendar_sync — ICS feed → planned meetings (see README.md).

Public surface: ``parse_ics`` / ``sync_user`` (pure logic), the production I/O adapters
``fetch_ics`` / ``fetch_configs``, and the shared one-user pass ``run_user_sync`` (+ stamp
helpers) used by BOTH the entrypoint's background poll loop and the user-facing sync-now edge.
"""
from .adapters import fetch_configs, fetch_ics
from .service import parse_ics, sync_user


def __getattr__(name):  # lazy: runner imports back from this package
    if name in ("run_user_sync", "store_stamp", "read_stamp"):
        from . import runner
        return getattr(runner, name)
    raise AttributeError(name)


__all__ = ["parse_ics", "sync_user", "fetch_ics", "fetch_configs",
           "run_user_sync", "store_stamp", "read_stamp"]
