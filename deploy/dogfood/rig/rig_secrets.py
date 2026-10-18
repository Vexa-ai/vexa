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
* **Legacy plaintext is migrated, then removed — EAGERLY, at process start.** A
  ``~/.storm/<name>.json`` written by an older rig is read, sealed, verified by reading it back,
  and only then unlinked. A migration that deletes before it verifies is a data-loss bug wearing a
  security fix.

  It used to happen on first READ of each store, which is correct per store and wrong for a
  deployment: after the restart that shipped this module, only ``mcp-tokens`` had been read, so
  only ``mcp-tokens`` was sealed and ``user-api-keys.json`` — every user's gateway API key — sat in
  plaintext until some later call happened to want it. The migration had run. Nothing said it had
  not finished, and nothing could, because no list of the stores existed anywhere. ``STORES`` is
  that list, ``migrate_all()`` walks it at import, and both are gated by a test.
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

#: EVERY store this module owns. The registry is half the fix for the lazy migration: without it
#: there was no list to walk at start, and no way for anyone to ask whether the migration had
#: finished. A new store that is not named here migrates late and silently, which is exactly the
#: defect — so `tests/test_migration.py` fails if a caller uses a store name this tuple does not
#: hold, and every reader below goes through `read`, which is what makes that check total.
STORES = (
    "mcp-tokens",        # vxa_mcp_ bearer -> {uid, email}
    "user-api-keys",     # uid -> the gateway API key minted for them
    "oauth/logins",      # setup code -> a short-lived login record
    "oauth/email-codes",  # address -> the live 6-digit sign-in code
    "oauth/regimes",     # uid -> {mode}: cloud or local
    "oauth/clients",     # dynamic client registration (RFC 7591)
    "oauth/codes",       # authorization codes, single use
    "oauth/tokens",      # OAuth bearer tokens with their audience and expiry
)


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


def _load_agent_module(relpath: str, name: str):
    """Load ONE file out of ``core/agent`` — by path, deliberately not as a package member.

    2026-09-03, live: the rig was down 2.5 minutes on a missing ``pydantic_settings``. It was
    never a dependency of anything the rig uses. ``shared/git_redaction.py`` imports ``re`` and
    nothing else; ``control_plane/secret_store.py`` says of itself *"stdlib only. No new dependency
    lands in the control-plane image for this"*. What pulled pydantic in was ``import
    shared.git_redaction`` running ``shared/__init__.py``, which re-exports ``Settings`` from
    ``shared.config``. The rig was paying for the whole agent control plane to get two regex
    functions and an envelope format — and paying at BOOT, where the bill is an outage.

    So the two files are loaded directly, under private names, executing no package ``__init__``.
    The safety of that rests on their staying stdlib-pure, which is a contract with another lane
    rather than an assumption: ``tests/test_runtime_deps.py`` fails at the gate, naming the module,
    if either grows a third-party import — instead of the rig failing at its next restart, which
    is how this was found the first time.
    """
    import importlib.util

    src = pathlib.Path(agent_src()) / relpath
    spec = importlib.util.spec_from_file_location(name, src)
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"the rig cannot load {relpath} from {agent_src() or '<unset>'}. Point VEXA_AGENT_SRC "
            "at the checkout's core/agent directory. This is not optional — it carries the "
            "credential encryption and the redactor."
        )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001 — a deployment input, named in the message
        sys.modules.pop(name, None)
        raise RuntimeError(
            f"the rig cannot load {src}: {type(exc).__name__}: {exc}. If this is a missing "
            "third-party module, that file is no longer stdlib-pure and the rig must declare it "
            "in deploy/dogfood/rig/pyproject.toml — see tests/test_runtime_deps.py."
        ) from exc
    return mod


_git_redaction = _load_agent_module("shared/git_redaction.py", "_rig_git_redaction")
_secret_store = _load_agent_module("control_plane/secret_store.py", "_rig_secret_store")

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


def migrate_all() -> dict:
    """Seal EVERY legacy plaintext store now, and say what was found. Never raises.

    Returns ``{"sealed": [...], "already": [...], "not_ours": [...], "failed": {...}}``.

    ``not_ours`` is deliberate. `witness-data.json` was sitting in the live state directory beside
    the stores and this module has no business sealing or deleting it — but staying silent about it
    would repeat the original mistake one level up, where "the migration ran" was true and "the
    plaintext is gone" was not. An operator reading this line sees what is still in the clear.
    """
    report = {"sealed": [], "already": [], "not_ours": [], "failed": {}}
    for name in STORES:
        try:
            had_plaintext = _legacy_path(name).is_file()
            read(name)                      # seals + verifies + unlinks when a legacy file is there
            if not had_plaintext:
                report["already"].append(name)
            elif _legacy_path(name).is_file():
                report["failed"][name] = "sealed copy did not read back; plaintext kept"
            else:
                report["sealed"].append(name)
        except Exception as exc:            # noqa: BLE001 — a bad store must not stop the others
            report["failed"][name] = f"{type(exc).__name__}: {exc}"
    owned = {f"{n}.json" for n in STORES}
    try:
        for p in sorted(STATE_DIR.rglob("*.json")):
            if p.is_file() and str(p.relative_to(STATE_DIR)) not in owned:
                report["not_ours"].append(str(p.relative_to(STATE_DIR)))
    except OSError:
        pass
    return report


def _migrate_at_import() -> None:
    """Run the migration when this module loads — which is process start for every entry point.

    At import rather than from a `main()` because there are three entry points into this file's
    consumers (the server, `vexa_oauth`, and the tests) and a migration you have to remember to
    call is one that gets called from two of them. It is bounded work — eight small files — and it
    cannot raise: an unstartable rig is a worse outcome than a late migration.
    """
    try:
        report = migrate_all()
    except Exception as exc:                # noqa: BLE001
        print(f"[rig_secrets] migration skipped: {type(exc).__name__}: {exc}", flush=True)
        return
    if report["sealed"] or report["failed"] or report["not_ours"]:
        print(f"[rig_secrets] sealed={report['sealed']} failed={report['failed']} "
              f"plaintext-not-ours={report['not_ours']}", flush=True)


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



_migrate_at_import()
