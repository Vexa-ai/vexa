"""The audit expires on schedule: crm-api calls ``crm_purge_audit()`` once an hour.

The function is the migrations path's (``migrate.audit_purge_function_sql``): it carries the period
``CRM_AUDIT_RETENTION_DAYS`` set at ``crm.migrate init`` and is the only way an audit row can go.
crm-api's role may execute it and can neither choose the period nor delete a row itself, so
calling it here can never remove anything younger than what the operator chose to keep. Every
replica may run it: a second call in the same hour finds nothing to remove."""
import logging
import threading

from sqlalchemy import text

PURGE_EVERY_S = 3600.0

_log = logging.getLogger('crm.retention')


def purge_audit(engine):
    """One purge: how many audit rows were past the period and are gone."""
    with engine.begin() as c:
        return int(c.execute(text('SELECT crm_purge_audit()')).scalar_one() or 0)


def run(engine, *, every_s=PURGE_EVERY_S, stop=None):
    """Purge now, then every ``every_s`` until ``stop`` is set. A failed purge is logged by its
    exception type (a driver message can carry SQL values) and tried again next time."""
    stop = stop or threading.Event()
    while True:
        try:
            n = purge_audit(engine)
            if n:
                _log.info('audit retention: %d row(s) past the period removed', n)
        except Exception as exc:  # noqa: BLE001 — the service keeps serving; the next hour retries
            _log.error('audit retention pass failed (%s)', type(exc).__name__)
        if stop.wait(every_s):
            return


def start(engine, *, every_s=PURGE_EVERY_S):
    """The purge loop on a daemon thread beside the service; returns its stop event."""
    stop = threading.Event()
    threading.Thread(target=run, args=(engine,), kwargs={'every_s': every_s, 'stop': stop},
                     name='crm-audit-retention', daemon=True).start()
    return stop
