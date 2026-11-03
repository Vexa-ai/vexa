"""Operator command: store or remove the signal model's key (role `signals`, `routes_signals.py`).

    python -m credential_broker.signals_key put <name>      # the key on stdin
    python -m credential_broker.signals_key delete <name>

Run inside the broker's container (same environment, same store). The value is read from stdin so
it never appears in a process list or a shell history, and nothing here prints it. The company
policy then names it: `model_signals: {adapter: jev, route: typesafe/jev-1.13, secret_ref:
broker:signals/<name>}`.
"""
from __future__ import annotations

import re
import sys

from .main import build_store
from .routes_signals import OWNER, record_path
from .settings import load

_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")


def main(argv=None, stdin=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("put", "delete") or not _NAME.match(args[1]):
        print("usage: python -m credential_broker.signals_key put|delete <name>  (lowercase name; the key on stdin)",
              file=sys.stderr)
        return 2
    action, name = args
    store = build_store(load())
    if action == "delete":
        store.delete(record_path(name))
        print(f"signal-model key {name!r} removed")
        return 0
    value = (stdin or sys.stdin).read().strip()
    if not value:
        print("refused: no key on stdin", file=sys.stderr)
        return 2
    store.put(record_path(name), {"value": value, "owner": OWNER})
    print(f"signal-model key {name!r} stored ({len(value)} characters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
