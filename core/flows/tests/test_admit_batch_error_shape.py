"""POST /events/batch never echoes an exception's message to the admin caller — CodeQL
py/stack-trace-exposure, alert #252 on PR #1456 (`core/flows/src/flows_integrations/flows_api.py`,
the `except Exception as e` in `admit_batch`).

Before the fix, one bad row's `error` field was `f"{type(e).__name__}: {e}"[:200]` — `str(e)` is
whatever the failing driver or the failing row put in the exception's message, and there is no
class of exception this route can guarantee is safe to echo: a DB driver's `OperationalError`
carries the DSN it tried, a validation error can carry another row's field value. The response goes
to an admin caller over the network, not to a log.

OFFLINE, same shape as `test_health.py` and `test_subject_bearer.py`: `flows_api` reads its
credentials and builds its app at import, so they are set immediately before the import and
restored immediately after — process-wide poison otherwise, whichever test module imports first.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "sqlite://"}
_saved = {k: os.environ.get(k) for k in _ENV}
os.environ.update(_ENV)
try:
    from flows_integrations import flows_api as fa  # noqa: E402
finally:
    for _k, _v in _saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

OPERATOR = fa.API_KEY
SECRET = "sk-super-secret-do-not-echo-me-4f9a2b"


@pytest.fixture
def client():
    return TestClient(fa.app, raise_server_exceptions=False)


def _batch():
    return {"meetings": [{"url": "https://m.test/a", "organizer": "a@b.test", "start": 1.0,
                           "title": "row"}]}


def test_a_row_that_fails_with_a_secret_looking_message_does_not_echo_it(client, monkeypatch, caplog):
    """RED before the fix: `admit()` raises with a secret in the message; the OLD code sliced
    `f"{type(e).__name__}: {e}"[:200]` straight into the JSON body, so the secret shipped to the
    caller. GREEN after: the body carries only the exception's TYPE and a stable `error_code`; the
    secret appears nowhere in the response, and the full detail is logged server-side instead,
    named by `source_event_id`."""

    def _boom(*a, **k):
        raise RuntimeError(f"connection failed: postgresql://postgres:{SECRET}@postgres:5432/vexa")

    monkeypatch.setattr(fa, "admit", _boom)

    import logging
    caplog.set_level(logging.ERROR, logger="flows_integrations.flows_api")

    r = client.post("/events/batch", json=_batch(),
                     headers={"X-Flows-Operator-Key": OPERATOR})

    assert r.status_code == 202
    body = r.json()
    raw = r.text

    # the secret never reaches the wire, in any field, at any nesting
    assert SECRET not in raw

    row = body["meetings"][0]
    assert row["error"] == "RuntimeError"          # typed: the exception class, nothing more
    assert row["error_code"] == "admit_failed"      # stable: a caller can branch/file on this
    assert SECRET not in row["error"]
    assert body["failed"] == 1

    # the full detail — secret included, via logger.exception's traceback — lands in the server
    # log, addressable by source_event_id. caplog.text renders exc_info, unlike record.getMessage().
    assert SECRET in caplog.text
    assert row["source_event_id"] in caplog.text
