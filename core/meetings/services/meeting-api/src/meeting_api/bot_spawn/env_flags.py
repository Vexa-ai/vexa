"""Boolean env flags that survive a set-but-empty value.

``os.getenv("TRANSCRIBE_ENABLED", "true")`` only falls back to the default when the key is
ABSENT. An env-file line with no value — ``TRANSCRIBE_ENABLED=``, which ``.env.example`` ships and
``docker run --env-file`` passes through verbatim — resolves to ``""``, and ``"" == "true"`` is
False. The default silently inverts.

That is not academic: it is the v0.12.5 release-witness failure. Lite's ``make up`` runs
``docker run --env-file $(ENV_FILE)`` and does not ``-e``-override these keys, so every Lite
self-host seeded from ``.env.example`` spawned capture-only bots — a bot that joins, behaves
normally, and transcribes nothing, with no error anywhere. Compose is accidentally immune because
``${TRANSCRIBE_ENABLED:-true}`` treats empty as unset; Lite has no such rescue.

config.v1 (``when_unconfigured``) states the contract these flags must keep: *bots must opt out
explicitly* to spawn capture-only. An empty string is not an explicit opt-out, and neither is a
typo — so only a recognized false value opts out. Anything unrecognized keeps the default and warns
rather than silently disabling the product's core value.
"""

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

_TRUE = ("true", "1", "yes", "on")
_FALSE = ("false", "0", "no", "off")


def env_flag(name: str, default: bool = True, raw: Optional[str] = None) -> bool:
    """Resolve ``name`` as a boolean, treating unset and set-but-empty alike.

    ``raw`` is an injection seam for tests; production passes ``None`` and reads the environment.
    Vocabulary matches the request-body parsers in ``router.py`` so ``TRANSCRIBE_ENABLED=1`` and
    ``transcribe_enabled: "1"`` cannot disagree.
    """
    value = os.getenv(name) if raw is None else raw
    if value is None or not value.strip():
        return default
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    log.warning(
        "%s=%r is not a recognized boolean (%s / %s) — keeping the default %s. "
        "An unrecognized value is not an explicit opt-out.",
        name, value, "/".join(_TRUE), "/".join(_FALSE), default,
    )
    return default
