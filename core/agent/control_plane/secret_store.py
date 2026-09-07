"""secret_store.py — the ONE encrypted-at-rest store every server-side credential lands in.

Before this module a saved PAT sat in plaintext under ``<root>/.secrets/<subject>.ghtoken``. That was
declared (``git_credentials``: "Plaintext at rest — like the webhook secret") and it was the level the
owner had chosen; it is no longer, because the same store now also holds **deploy-key private keys**,
which are write credentials on somebody else's repository.

The shape:

  * **One server-side key.** ``VEXA_SECRETS_KEY`` (config contract) when the operator supplies one;
    otherwise a key is generated on first use and written ``0600`` to ``<root>/.secrets/.master.key``
    (its directory ``0700``). Self-hosters get encryption without configuring anything — but be honest
    about what the generated default buys: it sits in the same directory as the ciphertext, so it
    defends against a stray file read, a mis-scoped mount or a log, and NOT against a stolen volume or
    backup. Any deployment that syncs or backs up the workspace store should set the env var, which is
    the whole reason it exists.
  * **Encrypt-then-MAC, stdlib only.** No new dependency lands in the control-plane image for this, so
    the construction is built from ``hashlib``/``hmac``: per-secret random salt → two subkeys by
    HMAC-SHA256 (encryption / authentication, domain-separated) → a SHA-256 counter keystream XORed
    over the plaintext → HMAC-SHA256 over ``salt || ciphertext``. Verified with
    ``hmac.compare_digest`` BEFORE decryption, so a tampered or foreign-key file reads as *absent*,
    never as garbage.
  * **Never logged.** Nothing here logs a value, and the callers (``workspace_git_sync``,
    ``workspace_attach``) already redact the credential out of git's own output (P15).

A missing/corrupt/undecryptable secret is ``None`` — "no credential", never an exception on the git
hot path. ``put`` is atomic (write-temp + replace) so a crashed write cannot leave a half-secret.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets as _secrets
from pathlib import Path
from typing import Mapping, Optional

log = logging.getLogger(__name__)

SECRETS_DIRNAME = ".secrets"          # dot-prefixed ⇒ every workspace scan skips it; not a git tree
MASTER_KEY_FILENAME = ".master.key"   # the generated-on-first-boot key, when the operator sets none
_ENVELOPE_VERSION = "v1"
_NAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,128}(/[A-Za-z0-9_.@-]{1,128})*$")  # path-safe, no traversal


ENV_KEY_NAME = "VEXA_SECRETS_KEY"   # the operator-supplied key (declared in config.v1.json)
#: The deployment profile, spelled the way this repository already spells it
#: (`transcription/main.py:154`, `vexa_mcp/app.py:587`): unset means `development`, and only the
#: exact word `production` is production.
ENV_PROFILE_NAME = "VEXA_ENV"


class SecretStoreUnconfigured(RuntimeError):
    """A production deployment that never gave the store a key to seal with (R-E08).

    Raised on the WRITE path only. The docstring at the top of this module is honest that a
    generated key sits beside the ciphertext and therefore defends a stray read and not a stolen
    volume — but `VEXA_SECRETS_KEY` appeared in no compose file, no `.env.example` and no helm
    values, so **no shipped deployment set it** and every deployment quietly took the weaker
    property while the documentation asserted the stronger one. In production that is now a refusal
    naming the fix, because a security claim nobody can act on is worse than no claim."""


UNCONFIGURED_SENTENCE = (
    "the credential store has no key to seal with: set VEXA_SECRETS_KEY (`openssl rand -hex 32`) "
    "on this deployment. A key generated here would sit in the same directory as the ciphertext, "
    "which is not encryption at rest in any sense worth claiming — outside production it is "
    "generated with a warning instead."
)


def is_production(env: "Mapping[str, str] | None" = None) -> bool:
    """Is this a production deployment? Only the exact word counts — `staging`, `dev`, a typo and an
    unset value are all "not production", so a box nobody declared never fails closed on a property
    it never claimed."""
    src = os.environ if env is None else env
    return str(src.get(ENV_PROFILE_NAME, "development") or "").strip().lower() == "production"


def configured_key(explicit: str = "") -> str:
    """The operator's key: an explicit argument wins, else ``VEXA_SECRETS_KEY`` from the environment —
    the same variable ``Settings.secrets_key`` declares, read here so every call site (routes, MCP,
    hot-path git ops) gets it without having to thread ``settings`` through the credential modules."""
    return (explicit or os.environ.get(ENV_KEY_NAME, "") or "").strip()


def secrets_dir(root: str | Path) -> Path:
    return Path(root) / SECRETS_DIRNAME


def _secret_path(root: str | Path, name: str) -> Optional[Path]:
    """``<root>/.secrets/<name>.enc`` — or None for a name that is not path-safe (never traverse)."""
    if not name or not _NAME_RE.match(name) or ".." in name:
        return None
    return secrets_dir(root) / f"{name}.enc"


def _harden(p: Path) -> None:
    """Owner-only on the file and its directory — best-effort (a filesystem may not honor modes)."""
    try:
        p.chmod(0o600)
        p.parent.chmod(0o700)
    except OSError:
        log.debug("could not chmod secret store entry", exc_info=True)


def read_key(root: str | Path, configured: str = "") -> Optional[bytes]:
    """The key to open secrets under ``root``, WITHOUT creating one — or ``None`` when there is none.

    Split out of ``master_key`` because ``get()`` used to call it, so a pure READ generated and wrote
    ``.master.key`` (R-E13). Two consequences, and the second is the expensive one: a read created
    server-side state, and when ``VEXA_SECRETS_KEY`` had been set at seal time and was later rotated
    or unset, the read MINTED A FRESH KEY, ``unseal`` returned ``None``, and ``has()`` answered
    ``False`` — telling the operator the credential did not exist when in fact it could not be
    decrypted. Re-pasting the PAT then overwrote a perfectly good envelope."""
    supplied = configured_key(configured)
    if supplied:
        return hashlib.sha256(supplied.encode("utf-8")).digest()
    try:
        raw = (secrets_dir(root) / MASTER_KEY_FILENAME).read_bytes().strip()
    except OSError:
        return None
    return hashlib.sha256(raw).digest() if raw else None


def master_key(root: str | Path, configured: str = "") -> bytes:
    """The server-side key every secret under ``root`` is encrypted with, CREATING it if absent.

    ``configured`` (``VEXA_SECRETS_KEY``) wins when set — that is the operator holding the key outside
    the data volume. Otherwise the key is READ from ``<root>/.secrets/.master.key``, and GENERATED
    there (32 random bytes, ``0600``) the first time anything is stored. Generation is racy-safe: a
    concurrent writer's file wins on re-read, so two boots cannot end up with two keys.

    **Only the WRITE path may call this.** Reads go through ``read_key`` — see its docstring.

    IN PRODUCTION IT REFUSES rather than generating (R-E08): see ``SecretStoreUnconfigured``. Outside
    production it generates and says so, every time, at WARNING — the store's honest level of
    protection is a thing an operator should have to read past, not discover from a docstring."""
    existing = read_key(root, configured)
    if existing is not None:
        return existing
    if is_production():
        raise SecretStoreUnconfigured(UNCONFIGURED_SENTENCE)
    log.warning(
        "%s is unset, so the credential store is generating a key UNDER %s — beside the ciphertext "
        "it seals. That defends a stray read, not a stolen volume or a backup. Acceptable in "
        "development; in production this is a refusal. Set %s to hold the key outside the data "
        "volume.", ENV_KEY_NAME, secrets_dir(root), ENV_KEY_NAME)
    kf = secrets_dir(root) / MASTER_KEY_FILENAME
    kf.parent.mkdir(parents=True, exist_ok=True)
    fresh = base64.b64encode(_secrets.token_bytes(32))
    tmp = kf.with_name(f"{kf.name}.{os.getpid()}.tmp")
    tmp.write_bytes(fresh)
    _harden(tmp)
    try:
        os.link(tmp, kf)          # link fails if another boot already created it → theirs wins
    except FileExistsError:
        pass
    except OSError:               # filesystems without hard links: last writer wins, still one file
        if not kf.exists():
            os.replace(tmp, kf)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    _harden(kf)
    return hashlib.sha256(kf.read_bytes().strip()).digest()


def _subkeys(key: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """Domain-separated encryption + authentication subkeys for ONE secret."""
    enc = hmac.new(key, b"vexa.secret.enc/" + salt, hashlib.sha256).digest()
    mac = hmac.new(key, b"vexa.secret.mac/" + salt, hashlib.sha256).digest()
    return enc, mac


def _keystream(enc: bytes, n: int) -> bytes:
    """SHA-256 in counter mode — ``H(enc || counter)`` blocks, truncated to ``n`` bytes."""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(enc + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:n])


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def seal(value: str, key: bytes) -> str:
    """``v1.<salt>.<ciphertext>.<mac>`` — encrypt-then-MAC, fresh 16-byte salt per call (so the same
    secret written twice produces two different envelopes)."""
    salt = _secrets.token_bytes(16)
    enc, mac_key = _subkeys(key, salt)
    plain = value.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(plain, _keystream(enc, len(plain))))
    tag = hmac.new(mac_key, salt + ct, hashlib.sha256).digest()
    return ".".join((_ENVELOPE_VERSION, _b64(salt), _b64(ct), _b64(tag)))


def unseal(envelope: str, key: bytes) -> Optional[str]:
    """The plaintext, or ``None`` when the envelope is malformed, truncated, tampered with, or sealed
    under a DIFFERENT key. The MAC is checked before a single byte is decrypted."""
    try:
        version, salt_b64, ct_b64, tag_b64 = envelope.strip().split(".")
        if version != _ENVELOPE_VERSION:
            return None
        salt, ct, tag = _unb64(salt_b64), _unb64(ct_b64), _unb64(tag_b64)
    except (ValueError, TypeError):
        return None
    enc, mac_key = _subkeys(key, salt)
    if not hmac.compare_digest(tag, hmac.new(mac_key, salt + ct, hashlib.sha256).digest()):
        return None
    try:
        return bytes(a ^ b for a, b in zip(ct, _keystream(enc, len(ct)))).decode("utf-8")
    except UnicodeDecodeError:
        return None


def put(root: str | Path, name: str, value: Optional[str], *, key_env: str = "") -> bool:
    """Store (or, with a falsy ``value``, DELETE) one secret. Returns True when a secret is now held.
    Atomic: the envelope is written to a temp file and ``os.replace``d into place."""
    p = _secret_path(root, name)
    if p is None:
        raise ValueError("invalid secret name")
    val = (value or "").strip()
    if not val:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return False
    # THE KEY FIRST, BEFORE ANY DISK IS TOUCHED. A production refusal (R-E08) that had already
    # created the store directory would leave the deployment looking half-configured to the next
    # reader — and the whole point of the refusal is that nothing about this store is half-done.
    key = master_key(root, key_env)
    p.parent.mkdir(parents=True, exist_ok=True)
    envelope = seal(val, key)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(envelope, encoding="utf-8")
    _harden(tmp)
    os.replace(tmp, p)
    _harden(p)
    return True


#: What ``state`` can answer. ``UNREADABLE`` is the one this store could not say before: an envelope
#: is there and this key does not open it, which is an OPERATOR problem (a rotated or unset
#: ``VEXA_SECRETS_KEY``) and not the "you never saved a credential" that ``ABSENT`` means.
ABSENT, HELD, UNREADABLE = "absent", "held", "unreadable"


def _envelope(root: str | Path, name: str) -> Optional[str]:
    p = _secret_path(root, name)
    if p is None:
        return None
    try:
        envelope = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return envelope or None


def get(root: str | Path, name: str, *, key_env: str = "") -> Optional[str]:
    """The stored secret, or ``None`` (absent · unreadable · wrong key · tampered). Never raises —
    this sits on the git hot path, where "no credential" is an ordinary answer.

    A READ, all the way down: no key is generated here (R-E13). Ask ``state`` when the difference
    between "there is none" and "this key does not open it" is what the caller needs."""
    envelope = _envelope(root, name)
    if envelope is None:
        return None
    key = read_key(root, key_env)
    if key is None:
        log.warning("a sealed secret is held under %r and no key is configured to open it", name)
        return None
    out = unseal(envelope, key)
    if out is None:
        log.warning("a sealed secret under %r cannot be decrypted with the current key "
                    "(rotated or unset VEXA_SECRETS_KEY?) — it is NOT absent", name)
    return out


def state(root: str | Path, name: str, *, key_env: str = "") -> str:
    """``absent`` · ``held`` · ``unreadable`` — the three answers ``has()`` collapses into a bool.

    The collapse is what made a rotated key look like a missing credential, so the surfaces that
    ask a person to re-paste a PAT should ask this instead of ``has``."""
    envelope = _envelope(root, name)
    if envelope is None:
        return ABSENT
    key = read_key(root, key_env)
    if key is None or unseal(envelope, key) is None:
        return UNREADABLE
    return HELD


def has(root: str | Path, name: str, *, key_env: str = "") -> bool:
    """Whether a READABLE secret is held under ``name`` (a file we cannot decrypt is not one)."""
    return state(root, name, key_env=key_env) == HELD


def delete(root: str | Path, name: str) -> bool:
    """Remove a secret. True when something was removed."""
    p = _secret_path(root, name)
    if p is None:
        return False
    try:
        p.unlink()
        return True
    except OSError:
        return False
