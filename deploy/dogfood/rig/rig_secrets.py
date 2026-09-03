"""rig_secrets.py — the rig's ONE credential store, and its one redactor.

Two defects in the 2026-09-02 line review live here, and they are the same defect twice: the rig
kept credentials in plaintext JSON at the default umask (R-D05) and every store was an unlocked
read-modify-write with a truncating save (R-D09). On the live host that produced
``-rw-rw-r-- ~/.storm/user-api-keys.json`` — every user's gateway key, group- and world-readable —
beside an encrypted store (``control_plane/secret_store.py``) that decision 25 declared and the rig
used for nothing.

What this module is:

* **The store is sealed.** One logical map = one encrypt-then-MAC envelope written through
  ``secret_store`` (``<state>/.secrets/<name>.enc``, ``0600`` in a ``0700`` directory, atomic
  write-temp-and-replace). Nothing here writes a credential a reader could use.
* **The store is locked.** ``update()`` holds an exclusive ``flock`` across read-modify-write, so
  two concurrent sign-ins cannot lose a token, and the write underneath it is atomic, so a crash
  mid-write cannot empty the store and log everybody out.
* **Legacy plaintext is migrated, then removed.** A ``~/.storm/<name>.json`` written by an older
  rig is read once, sealed, verified by reading it back, and only then unlinked. A migration that
  deletes before it verifies is a data-loss bug wearing a security fix.
* **One redactor, imported not copied.** ``redact``/``looks_like_token`` come from
  ``shared/git_redaction.py`` — the same detector agent-api uses — because the rig's hand-rolled
  six-prefix copy had already drifted past ``glpat-`` and bare userinfo URLs (R-D14).

Both imports are HARD. A rig that cannot reach ``core/agent`` has no encryption and no redactor,
and a security control that disappears when a path is wrong is worse than an absent one: it is an
absent one that reads as present. ``VEXA_AGENT_SRC`` names the tree when the default cannot find it.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

HOME = pathlib.Path.home()

#: Where the rig keeps its state. One variable so a test never touches a live store.
STATE_DIR = pathlib.Path(os.environ.get("VEXA_RIG_STATE_DIR") or (HOME / ".storm"))


def agent_src() -> str:
    """The importable ``core/agent`` tree — ``VEXA_AGENT_SRC``, else the nearest one above us.

    Searched rather than counted in ``parents[n]``: this file is scheduled to move into
    ``core/mcp`` (seam item B1) and a hardcoded depth would break silently on the day it does.
    """
    named = (os.environ.get("VEXA_AGENT_SRC") or "").strip()
    if named:
        return named
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "core" / "agent"
        if (cand / "shared" / "git_redaction.py").is_file():
            return str(cand)
    return str(here.parents[3] / "core" / "agent") if len(here.parents) > 3 else ""


def _import_agent(module: str):
    src = agent_src()
    if src and src not in sys.path:
        sys.path.insert(0, src)
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as exc:  # pragma: no cover — a deployment input, named in the message
        raise RuntimeError(
            f"the rig cannot import {module} from {src or '<unset>'}: {exc}. "
            "Point VEXA_AGENT_SRC at the checkout's core/agent directory. This is not optional — "
            "it carries the credential encryption and the redactor."
        ) from exc


_git_redaction = _import_agent("shared.git_redaction")
_secret_store = _import_agent("control_plane.secret_store")

redact = _git_redaction.redact
looks_like_token = _git_redaction.looks_like_token
TOKEN_PREFIXES = _git_redaction.TOKEN_PREFIXES
MASK = _git_redaction.MASK


def harden(p: pathlib.Path) -> None:
    """Owner-only on the file and its directory. Best-effort: a filesystem may not honor modes."""
    try:
        if p.is_dir():
            p.chmod(0o700)
        else:
            p.chmod(0o600)
            p.parent.chmod(0o700)
    except OSError:
        pass


def _legacy_path(name: str) -> pathlib.Path:
    """Where an older rig wrote this map in plaintext (``mcp-tokens`` → ``mcp-tokens.json``)."""
    return STATE_DIR / f"{name}.json"


def _migrate(name: str) -> dict:
    """Seal a legacy plaintext map, VERIFY the readback, then remove the plaintext. Returns it."""
    legacy = _legacy_path(name)
    try:
        raw = legacy.read_text()
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict) or not data:
        return data if isinstance(data, dict) else {}
    _seal(name, data)
    if _unseal(name) != data:          # never delete a plaintext store we could not re-read
        harden(legacy)
        return data
    try:
        legacy.unlink()
    except OSError:
        harden(legacy)
    return data


def _seal(name: str, data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    harden(STATE_DIR)
    _secret_store.put(STATE_DIR, name, json.dumps(data))


def _unseal(name: str):
    raw = _secret_store.get(STATE_DIR, name)
    if raw is None:
        return None
    try:
        out = json.loads(raw)
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def read(name: str) -> dict:
    """The sealed map under ``name`` — ``{}`` when absent, unreadable, or sealed under another key.

    Never raises: every caller here is on a request path where "no credentials" is an ordinary
    answer and an exception would be an outage.
    """
    got = _unseal(name)
    if got is not None:
        return got
    return _migrate(name)


def _lock_path(name: str) -> pathlib.Path:
    return _secret_store.secrets_dir(STATE_DIR) / f"{name.replace('/', '_')}.lock"


def update(name: str, fn):
    """Locked read-modify-write. ``fn(map)`` mutates in place (or returns a replacement map).

    The lock is an exclusive ``flock`` on a sibling file, held across BOTH halves. Without it the
    store was last-writer-wins over a whole map: two sign-ins in the same second lost one token,
    which is what ``mcp-tokens.json``'s three independent writers were doing.
    """
    lock = _lock_path(name)
    lock.parent.mkdir(parents=True, exist_ok=True)
    harden(lock.parent)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (ImportError, OSError):   # pragma: no cover — platforms without flock
            pass
        cur = read(name)
        out = fn(cur)
        new = out if isinstance(out, dict) else cur
        _seal(name, new)
        return new
    finally:
        os.close(fd)


def write(name: str, data: dict) -> dict:
    """Replace the whole map. Still locked — a replace races a read-modify-write just as badly."""
    return update(name, lambda _cur: data)


def signing_key(name: str, env: str = "") -> bytes:
    """A stable server-side signing key, from ``env`` when the operator sets one, else generated
    once into the sealed store. Used for the short-lived view tokens that replaced the durable
    bearer tokens the rig used to mint into URLs (R-D04)."""
    supplied = (os.environ.get(env, "") if env else "").strip()
    if supplied:
        return supplied.encode("utf-8")
    held = read(f"keys/{name}").get("k")
    if not held:
        import secrets as _s

        def _mint(d: dict) -> dict:
            d.setdefault("k", _s.token_urlsafe(32))     # under the lock: first writer wins
            return d

        held = update(f"keys/{name}", _mint).get("k", "")
    return str(held).encode("utf-8")
