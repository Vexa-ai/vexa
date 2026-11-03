"""Operator command: store or remove an outbound-mail credential (`routes_mail.py`, PRD 0.13.4 S2).

    python -m credential_broker.mail_secret put <name>      # the password on stdin
    python -m credential_broker.mail_secret delete <name>

Run inside the broker's container (same environment, same store). The value is read from stdin so it
never appears in a process list or a shell history, and nothing here prints it. The install then
names it: `VEXA_MAIL_SMTP_PASSWORD_REF=broker:mail/<name>` on flows.
"""
from __future__ import annotations

import re
import sys

from .main import build_store
from .routes_mail import OWNER, record_path
from .settings import load

_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")


def main(argv=None, stdin=None, store=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("put", "delete") or not _NAME.match(args[1]):
        print("usage: python -m credential_broker.mail_secret put|delete <name>  (lowercase name; the "
              "password on stdin)", file=sys.stderr)
        return 2
    action, name = args
    store = store if store is not None else build_store(load())
    if action == "delete":
        store.delete(record_path(name))
        print(f"mail credential {name!r} removed")
        return 0
    value = (stdin or sys.stdin).read().strip()
    if not value:
        print("refused: no value on stdin", file=sys.stderr)
        return 2
    store.put(record_path(name), {"value": value, "owner": OWNER})
    print(f"mail credential {name!r} stored ({len(value)} characters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
