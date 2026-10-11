"""global_layer.py — the COMPANY LAYER: the thin, optional organisation tier in `_global`.

Founder ruling, 2026-10-08: *"let's remove global setup at all so that there is no need to setup
global at all - let it be empty with no data - it's fine."* That reverses the 2026-09-02 ruling
(*"global needs to be setup by admin, it just should not let him start the service before that"*):
nothing waits for this layer any more. `_global` may stay empty for the life of an instance; no
sign-in, dispatch, flow or operator verb is refused for want of it.

When an admin DOES choose to write it, the shape is still thin — *"who the company is, principles,
objectives, structure, what is missing: a few short files every agent carries"* — and this module
owns three things about it:

  1. **What "written" MEANS** — the five files, and the one content rule on `README.md`: it opens
     with the company's name and one sentence of what it does, because those two lines are read out
     loud to strangers.
  2. **The verification** — read the store and answer what is present, what is missing, and which
     company the layer names (`state`).
  3. **The commit** — `_global` is a git repo on the store and an admin's acceptance is a commit
     authored by that admin, so an edit to what every agent carries is reviewable, diffable and
     revertable.

And it is agent-api's one reader of the two roles identity answers: the ADMIN ACCOUNT
(`is_admin_account`) — the only identity that writes `_global` — and the instance admin (`is_admin`),
a person, who reads `_global` like everybody else.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from shared.gitexec import run_git
from shared.marks import UNWRITTEN_MARK
from workspaces.shared import workspace_paths as wpaths

logger = logging.getLogger("agent_api.global_layer")

# The thin layer, in the order the setup conversation walks it. README FIRST and its first lines
# are the company's name and one sentence of what it does — everything downstream (the agent's
# self-introduction, the follow-up mail's opening) reads the company from there.
LAYER_FILES = ("README.md", "PRINCIPLES.md", "OBJECTIVES.md", "STRUCTURE.md", "MISSING.md")

# ── WHAT "NOT YET WRITTEN" MEANS ONCE THE LAYER ARRIVES SCAFFOLDED ───────────────────────────────
# Before `behavior/global/` existed, `_global` was an empty directory and EMPTINESS was the whole
# test: a file with nothing in it had not been written. A seeded placeholder is not empty, so on its
# own that test would have let an instance accept five files nobody had filled in.
#
# The replacement is a POSITIVE SIGNATURE, not a comparison against remembered bytes: every seed
# file carries this marker inside an HTML comment, and a file that still carries it is still the
# seed's. A hash of the shipped file would go stale the moment the seed's wording changed and would
# then silently call an untouched placeholder "written"; a marker the admin's editor cannot remove
# by accident cannot. Writing the file means deleting the comment, which the comment itself says.
# The literal lives in `shared/marks.py` because the worker reads it too; flows' copy and the seed
# files are held to it by `gate:fact-parity` (`scripts/parity.json`, fact `unwritten-marker`).
UNWRITTEN_MARKER = UNWRITTEN_MARK


def is_unwritten(text: str) -> bool:
    """Is this layer file still the seed's placeholder? (The marker is present.)"""
    return UNWRITTEN_MARKER in (text or "")

# The README's opening contract: a level-1 heading whose text is the company name, then at least one
# non-empty line of prose before any other heading. Two lines, and they are the two the product
# introduces itself with.
_H1 = re.compile(r"^#\s+(.+?)\s*$", re.M)


def company_of(readme: str) -> Optional[str]:
    """The company name the layer opens with, or None if the README does not name one.

    Deliberately strict about the SHAPE (an H1 on the first non-blank line) because this string is
    read out loud to strangers — "I'm Vexa, the meeting assistant at <company>" — and a heading
    that happens to say "Setup" would put that word in front of a customer."""
    for line in readme.splitlines():
        if not line.strip():
            continue
        m = _H1.match(line)
        if not m:
            return None
        name = m.group(1).strip()
        # A placeholder is not a company. These are the words the setup conversation itself uses
        # while it is still asking, so accepting them would put "Company" in front of a customer.
        if not name or name.lower() in {"company", "your company", "unknown", "tbd", "readme",
                                        "_global", "global"}:
            return None
        return name
    return None


def service_line_of(readme: str) -> Optional[str]:
    """The one sentence of what the company does — the first prose line under the H1."""
    seen_h1 = False
    for line in readme.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not seen_h1:
            seen_h1 = bool(_H1.match(line))
            if not seen_h1:
                return None
            continue
        if stripped.startswith("#"):
            return None          # a second heading before any prose: the sentence is missing
        if stripped.startswith(("<!--", ">")):
            continue             # a comment or a callout is not the sentence
        return stripped
    return None


def state(root: str | Path) -> dict:
    """What the company layer holds RIGHT NOW, read off the store.

    Returns `{ready, present, missing_files, company, service, reasons, is_repo, commits}`. `ready`
    is the verifier's verdict on an admin's acceptance; `reasons` says in words what is not yet
    true. An empty `_global` is simply not ready — which gates nothing."""
    path = Path(root)
    present, missing, unwritten = [], [], []
    for name in LAYER_FILES:
        # NOFOLLOW: `_global` is written by an admin's turn; a linked layer file is not written
        raw = wpaths.read_bytes_inside(path, name)
        body = raw.decode("utf-8", errors="replace") if raw is not None else ""
        if not body.strip():
            missing.append(name)
        elif is_unwritten(body):
            unwritten.append(name)
        else:
            present.append(name)
    raw = wpaths.read_bytes_inside(path, "README.md")
    readme = raw.decode("utf-8", errors="replace") if raw is not None else ""
    company = company_of(readme)
    service = service_line_of(readme)
    reasons = []
    if missing:
        reasons.append("these files are missing or empty: " + ", ".join(missing))
    if unwritten:
        reasons.append("these files are still the seed's placeholder — the setup conversation has "
                       "not written them yet: " + ", ".join(unwritten))
    if not company:
        reasons.append("README.md does not open with the company's name as its first heading")
    if not service:
        reasons.append("README.md does not carry one sentence of what the company does under that heading")
    return {
        "ready": not reasons,
        "present": present,
        # `missing_files` keeps its name and now carries BOTH shapes of "this file is not written":
        # absent-or-empty, and still-the-seed's. Every caller of this key — the wizard, the setup
        # ask, `routers/admin.py`'s 409 — is answering the question "what does the admin still have
        # to write", and for that question a placeholder is missing. `unwritten` names the second
        # shape separately for a caller that wants to say WHICH kind.
        "missing_files": missing + unwritten,
        "unwritten": unwritten,
        "company": company,
        "service": service,
        "reasons": reasons,
        "is_repo": (path / ".git").is_dir(),
        "commits": _commit_count(path),
    }


def _git(path: Path, *args: str, check: bool = False, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    # Through shared.gitexec: `_global` is mounted into every worker, so nothing its repository
    # configures may run in this process.
    return run_git(path, *args, check=check, env=env)


def _commit_count(path: Path) -> int:
    if not (path / ".git").is_dir():
        return 0
    r = _git(path, "rev-list", "--count", "HEAD")
    try:
        return int((r.stdout or "0").strip())
    except ValueError:
        return 0


def ensure_repo(root: str | Path) -> bool:
    """Make `_global` a git repo on the store, idempotently. Returns True if it initialised one.

    `_global` shipped as a BARE DIRECTORY: it was mounted into every worker and read on every turn,
    and nothing recorded who changed it or what it said yesterday. One admin edit changes how every
    agent in the deployment behaves (PRD §7.1) — that is the feature for a bank and the risk in the
    same breath — so it gets history, and history has to exist before the first write, not after."""
    path = Path(root)
    if not path.is_dir():
        raise FileNotFoundError(f"the organisation tier does not exist at {path}")
    if (path / ".git").is_dir():
        return False
    _git(path, "init", "-q", check=True)
    _git(path, "config", "user.email", "platform@vexa.local", check=True)
    _git(path, "config", "user.name", "vexa-platform", check=True)
    _git(path, "add", "-A")
    _git(path, "-c", "user.email=platform@vexa.local", "-c", "user.name=vexa-platform",
         "commit", "-q", "-m", "the organisation tier, before any company layer", "--allow-empty")
    logger.info("global_layer: initialised the _global git repo at %s", path)
    return True


def commit(root: str | Path, *, author_email: str, author_name: str, message: str) -> Optional[str]:
    """Commit whatever the admin's chat wrote into `_global`, AUTHORED BY THAT ADMIN.

    Returns the new commit sha, or None when there was nothing to commit (an idempotent re-run of
    the acceptance verb, which must not be an error). The author is the human, not the agent: the
    agent typed it, the admin accepted it, and the reviewable record has to name the person who is
    answerable for what every agent in the company will now carry."""
    path = Path(root)
    ensure_repo(path)
    _git(path, "add", "-A")
    status = _git(path, "status", "--porcelain")
    if not (status.stdout or "").strip():
        r = _git(path, "rev-parse", "HEAD")
        return (r.stdout or "").strip() or None
    r = _git(path, "-c", f"user.email={author_email}", "-c", f"user.name={author_name}",
             "commit", "-q", "-m", message)
    if r.returncode != 0:
        raise RuntimeError(f"could not commit the company layer: {(r.stderr or r.stdout)[:400]}")
    head = _git(path, "rev-parse", "HEAD")
    return (head.stdout or "").strip() or None


# ── the admin role: admin-api owns it; this is agent-api's ONE reader of it ─────────────────────

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_S = 15.0


def _admin_api(url: str, secret: str, path: str, *, timeout: float = 6.0) -> dict:
    req = urllib.request.Request(f"{url.rstrip('/')}{path}", headers={"X-Internal-Secret": secret})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def _ask_identity(settings, subject: str) -> Optional[dict]:
    """`/internal/users/{subject}/is-admin`, cached briefly — or None when identity cannot answer.
    The one hop both questions below ride, so they can never read two different answers."""
    url = (getattr(settings, "admin_api_url", "") or "").strip()
    try:
        secret = settings.internal_api_secret.get_secret_value()
    except Exception:  # noqa: BLE001
        secret = ""
    if not url or not secret or not str(subject).strip():
        return None
    key = f"is_admin::{url}::{subject}"
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    try:
        out = _admin_api(url, secret, f"/internal/users/{subject}/is-admin")
    except (urllib.error.URLError, OSError, ValueError) as e:  # noqa: PERF203
        logger.warning("global_layer: could not resolve the roles of subject=%s (%s) — treating as "
                       "NEITHER the instance admin NOR the admin account", subject, e)
        return None
    _CACHE[key] = (now + _CACHE_TTL_S, out)
    return out


def is_admin(settings, subject: str) -> bool:
    """Is this subject the INSTANCE admin? `users.data.is_admin` (or `VEXA_ADMIN_EMAILS`), asked of
    the service that owns it. A PERSON — it governs support surfaces (scaffolds, renames), and it no
    longer writes `_global`: that is :func:`is_admin_account`'s, and nobody else's. Fails closed."""
    out = _ask_identity(settings, subject)
    return bool(out and out.get("is_admin") is True)


def is_admin_account(settings, subject: str) -> bool:
    """Is this subject the deployment's ADMIN ACCOUNT — the one identity that controls `_global`?

    Founder ruling 2026-10-11: *"admin is a special account that has global only and this being the
    only one that has control to global"*. Identity owns the answer (admin-api
    `app/admin_account.py`: a row created as the admin account while `VEXA_ADMIN_ACCOUNT` names its
    address, and still named); this is agent-api's one reader of it. There is no list here and no
    override: the operator's door is that env key on admin-api, one writer. Fail closed — no admin
    account configured, identity unreachable, or a field missing (an older identity) all say no, and
    then nobody writes `_global`."""
    out = _ask_identity(settings, subject)
    return bool(out and out.get("admin_account") is True)
