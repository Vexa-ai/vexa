"""log_scrub.py — a credential in a query string never reaches a log line (security review 2026-10-17, M7).

`/ws` and the VNC relay still accept `?api_key=<key>` — ws.v1 publishes it, for clients that cannot
set a header (a browser `WebSocket`). The terminal never uses it (its server injects `x-api-key`), but
whoever does would leave the key in every access log the request passes: uvicorn's access line and
its WebSocket `accepted` line both print the full path with its query.

This filter rewrites those records before any handler formats them: the VALUE of `api_key`, `token`,
`access_token`, `key` and `apikey` query parameters becomes `[redacted]`, in the message and in every
string argument. It is installed on the uvicorn loggers once, by `create_app`; installing it twice is
a no-op. Withdrawing the query parameter itself is a ws.v1 change (a published wire), left to its own
decision; this closes the log leak now.
"""
from __future__ import annotations

import logging
import re

#: The query parameters whose value is a credential.
CREDENTIAL_PARAMS = ("api_key", "apikey", "access_token", "token", "key")
_PATTERN = re.compile(r"(?i)([?&](?:%s)=)[^&\s\"']*" % "|".join(CREDENTIAL_PARAMS))
REDACTED = "[redacted]"
#: The loggers that print a request's path with its query.
LOGGERS = ("uvicorn.access", "uvicorn.error", "uvicorn")


def scrub(text: str) -> str:
    """``text`` with every credential query value replaced by ``[redacted]``."""
    return _PATTERN.sub(lambda m: m.group(1) + REDACTED, text)


class QueryCredentialFilter(logging.Filter):
    """Rewrites a record's message and string arguments; never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(scrub(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: (scrub(v) if isinstance(v, str) else v) for k, v in record.args.items()}
        return True


def install() -> None:
    """Put the filter on every uvicorn logger that prints a path, once."""
    for name in LOGGERS:
        lg = logging.getLogger(name)
        if not any(isinstance(f, QueryCredentialFilter) for f in lg.filters):
            lg.addFilter(QueryCredentialFilter())
