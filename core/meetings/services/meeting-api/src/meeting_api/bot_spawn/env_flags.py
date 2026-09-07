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


class InvalidFlagValue(ValueError):
    """A caller-supplied flag value that is neither a bool nor a recognized boolean string."""

    def __init__(self, field: str):
        super().__init__(f"{field} must be a boolean")
        self.field = field


def resolve_spawn_flag(
    env_name: str,
    value: Optional[object] = None,
    *,
    default: bool = True,
    field: Optional[str] = None,
) -> bool:
    """THE single resolution for a spawn boolean, shared by every caller of ``request_bot``.

    An explicit caller value wins; otherwise the env decides (through ``env_flag``, so a
    set-but-empty value keeps the default rather than inverting it). The caller value is
    type-validated — a bool is honored, a recognized string is parsed, an empty string is an
    explicit False (a request body that says ``""`` asked for off), and anything else raises
    ``InvalidFlagValue`` rather than being ``bool()``-coerced (which turned ``"false"`` into True).

    It lives HERE, not in ``router.py``, because the HTTP route is not the only spawner: the
    auto-join sweep spawns the same bots and must resolve the same way. When the sweep read no
    default at all it inherited ``request_bot``'s ``recording_enabled=False`` and every
    calendar-joined bot silently skipped recording while every manual bot recorded (#1216, proven
    on stage rev 194 by meetings 26353/26354). One resolver is what makes that drift impossible.
    """
    if value is None:
        return env_flag(env_name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE:
            return True
        if v in _FALSE or v == "":
            return False
    raise InvalidFlagValue(field or env_name.lower())
