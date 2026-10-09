"""unit_input — who may put a message on a live worker's input stream.

A live worker takes whatever arrives on ``unit:<id>:in`` as its owner's next message. Redis alone
cannot say who wrote an entry: every service that holds the service connection may write any key.
So agent-api, the one writer, signs each entry with a key of that unit's own, and the worker runs
only the entries that verify:

* the key is HMAC-SHA256(``INTERNAL_API_SECRET``, ``vexa-unit-input.v1:<unit id>``) — agent-api
  derives it per unit and hands it to that unit's worker as :data:`KEY_ENV`; no other worker holds it,
  and the model's tools cannot read the worker's environment;
* an entry is ``{"turn": <json>, "sig": <hex HMAC-SHA256 of the turn, keyed with the unit key>}``.

A worker with no key runs no stream entry at all (fail closed); its entrypoint still runs.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Mapping, Optional

#: The environment variable carrying a worker's unit key (hex).
KEY_ENV = "VEXA_UNIT_IN_KEY"
_LABEL = b"vexa-unit-input.v1:"


def unit_key(secret: str, unit_id: str) -> str:
    """The input-stream key of one unit, as hex. Empty when there is no secret to derive it from."""
    if not secret or not unit_id:
        return ""
    return hmac.new(secret.encode("utf-8"), _LABEL + unit_id.encode("utf-8"), hashlib.sha256).hexdigest()


def _sig(key: str, turn: str) -> str:
    return hmac.new(bytes.fromhex(key), turn.encode("utf-8"), hashlib.sha256).hexdigest()


def signed_entry(key: str, body: Mapping) -> dict[str, str]:
    """The stream fields for one message to a unit: the turn JSON and, with a key, its signature."""
    turn = json.dumps(dict(body))
    return {"turn": turn, "sig": _sig(key, turn)} if key else {"turn": turn}


def verified_turn(key: str, fields: Mapping) -> Optional[dict]:
    """The message in a stream entry when its signature holds under ``key``; None otherwise —
    unsigned, forged, signed for another unit, unparseable, or no key to check with."""
    turn, sig = fields.get("turn"), fields.get("sig")
    if isinstance(turn, bytes):
        turn = turn.decode("utf-8", "replace")
    if isinstance(sig, bytes):
        sig = sig.decode("ascii", "replace")
    if not (key and isinstance(turn, str) and isinstance(sig, str)):
        return None
    try:
        if not hmac.compare_digest(_sig(key, turn), sig):
            return None
        msg = json.loads(turn)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None
