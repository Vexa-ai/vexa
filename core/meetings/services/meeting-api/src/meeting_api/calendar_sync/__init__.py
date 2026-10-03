"""calendar_sync — a user's calendar → planned meetings (see README.md).

Public surface: the READERS ``parse_ics`` / ``events_from_google`` / ``events_from_microsoft``,
which all emit the same ``{"events": [...], "cancelled_uids": [...]}``; ``sync_user`` (the upsert
pipeline, which cannot tell which reader produced its input); the production I/O adapters
``fetch_ics`` / ``fetch_configs``; and the shared one-user pass ``run_user_sync`` (+ stamp
helpers) used by BOTH the entrypoint's background poll loop and the user-facing sync-now edge.
"""
from .adapters import build_ics_client, fetch_configs, fetch_ics
from .providers import events_from_google, events_from_microsoft
from .service import parse_ics, sync_user


def __getattr__(name):  # lazy: runner imports back from this package
    if name in ("run_user_sync", "aggregate_stamps", "store_stamp", "read_stamp",
                "active_configs"):
        from . import runner
        return getattr(runner, name)
    raise AttributeError(name)


__all__ = ["parse_ics", "events_from_google", "events_from_microsoft", "sync_user",
           "fetch_ics", "fetch_configs", "build_ics_client",
           "run_user_sync", "aggregate_stamps", "store_stamp", "read_stamp",
           "active_configs"]
