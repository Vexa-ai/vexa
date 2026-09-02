"""global_layer.py — the COMPANY LAYER: what `_global` must hold before this Vexa serves anyone.

Founder ruling, 2026-09-02: *"global needs to be setup by admin, it just should not let him start
the service before that."* And, on the shape of it: `_global` is THIN — *"who the company is,
principles, objectives, structure, what is missing: a few short files every agent carries."* No
company workspace, no demo data. The substance of the company lives in ordinary workspaces that a
chat recombines by mounting several of them; `_global` is the one thing every agent carries.

This module owns three things and nothing else:

  1. **What "set up" MEANS** — the five files, and the one content rule on `README.md`. The rule
     exists because of a second founder ruling the same day: *"the first chat needs to present
     itself knowing about itself — which company it's from and what's their service."* An agent
     that opens by saying which company it belongs to can only do that if a human wrote the name
     down, so the gate does not lift on a README that does not carry one.
  2. **The verification** — read the store and answer what is present, what is missing, and which
     company the layer names. Nothing may mark itself ready: the marker is written only after this
     module has looked at the files.
  3. **The commit** — `_global` becomes a git repo on the store and every admin acceptance is a
     commit authored by that admin. Git supplies the firmness the PRD asks for (§3.2): an admin
     edit is reviewable, diffable and revertable, not a silent mutation of how every agent in the
     deployment behaves.

It deliberately does NOT own the gate VALUE. That lives in one row in admin-api's
`platform_settings` and is read by every service through admin-api — one source of truth, one
reader per service. This module verifies, then asks admin-api to flip it.
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

logger = logging.getLogger("agent_api.global_layer")

# The thin layer, in the order the setup conversation walks it. README FIRST and its first lines
# are the company's name and one sentence of what it does — everything downstream (the agent's
# self-introduction, the follow-up mail's opening) reads the company from there.
LAYER_FILES = ("README.md", "PRINCIPLES.md", "OBJECTIVES.md", "STRUCTURE.md", "MISSING.md")

# The gate's vocabulary, spelled the same way admin-api spells it.
COMPLETED = "completed"
MISSING = "missing"

# The one sentence a refused visitor sees. Quoted, never paraphrased — see admin-api's GATE_SENTENCE.
GATE_SENTENCE = "This Vexa is being set up by its administrator."

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
        # while it is still asking, so accepting them would let an unfinished chat lift the gate.
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
    is the verifier's verdict and the ONLY input to flipping the gate; `reasons` says in words what
    is not yet true, because a wizard that says "not ready" without saying why is a dead end for
    the one person who can fix it."""
    path = Path(root)
    present, missing = [], []
    for name in LAYER_FILES:
        f = path / name
        if f.is_file() and f.read_text(encoding="utf-8", errors="replace").strip():
            present.append(name)
        else:
            missing.append(name)
    readme = ""
    if (path / "README.md").is_file():
        readme = (path / "README.md").read_text(encoding="utf-8", errors="replace")
    company = company_of(readme)
    service = service_line_of(readme)
    reasons = []
    if missing:
        reasons.append("these files are missing or empty: " + ", ".join(missing))
    if not company:
        reasons.append("README.md does not open with the company's name as its first heading")
    if not service:
        reasons.append("README.md does not carry one sentence of what the company does under that heading")
    return {
        "ready": not reasons,
        "present": present,
        "missing_files": missing,
        "company": company,
        "service": service,
        "reasons": reasons,
        "is_repo": (path / ".git").is_dir(),
        "commits": _commit_count(path),
    }


def _git(path: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], check=check,
                          capture_output=True, text=True)


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


# ── the gate value: admin-api owns it; this is agent-api's ONE reader/writer of it ───────────────

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_S = 15.0


def _admin_api(url: str, secret: str, path: str, *, method: str = "GET",
               payload: Optional[dict] = None, timeout: float = 6.0) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{url.rstrip('/')}{path}", data=body, method=method,
                                 headers={"X-Internal-Secret": secret,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def instance_state(settings, *, force: bool = False) -> dict:
    """The gate as admin-api holds it: `{admin_exists, global_setup, company}`.

    Cached briefly because the mount builder consults it on EVERY dispatch. FAIL-CLOSED on every
    error path: an unreachable admin-api means `missing`, because the expensive direction is a
    worker mounting the organisation tier read-write for somebody whose admin role we could not
    actually confirm."""
    url = (getattr(settings, "admin_api_url", "") or "").strip()
    secret = ""
    try:
        secret = settings.internal_api_secret.get_secret_value()
    except Exception:  # noqa: BLE001 — a settings object without the field (tests)
        secret = ""
    key = f"instance::{url}"
    now = time.monotonic()
    if not force:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    fallback = {"admin_exists": True, "global_setup": MISSING, "company": None, "degraded": True}
    if not url or not secret:
        return fallback
    try:
        out = _admin_api(url, secret, "/internal/instance")
        out.setdefault("global_setup", MISSING)
    except (urllib.error.URLError, OSError, ValueError) as e:  # noqa: PERF203
        logger.warning("global_layer: could not read the instance gate (%s) — treating it as missing", e)
        return fallback
    _CACHE[key] = (now + _CACHE_TTL_S, out)
    return out


def is_admin(settings, subject: str) -> bool:
    """Is this subject the instance admin? `users.data.is_admin`, asked of the service that owns it.

    The env allow-list (`VEXA_GLOBAL_ADMIN_SUBJECTS`) stays as an OPERATOR OVERRIDE — a deployment
    that has locked itself out needs a door that does not depend on the database being right — but
    it is no longer the definition. It could not be: the admin is claimed at first sign-in, which
    happens long after the env was written."""
    override = {a.strip() for a in (getattr(settings, "global_admin_subjects", "") or "").split(",") if a.strip()}
    if str(subject) in override:
        return True
    url = (getattr(settings, "admin_api_url", "") or "").strip()
    try:
        secret = settings.internal_api_secret.get_secret_value()
    except Exception:  # noqa: BLE001
        secret = ""
    if not url or not secret or not str(subject).strip():
        return False
    key = f"is_admin::{url}::{subject}"
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return bool(hit[1].get("is_admin"))
    try:
        out = _admin_api(url, secret, f"/internal/users/{subject}/is-admin")
    except (urllib.error.URLError, OSError, ValueError) as e:  # noqa: PERF203
        logger.warning("global_layer: could not resolve the admin role for subject=%s (%s) — treating as NOT admin", subject, e)
        return False
    _CACHE[key] = (now + _CACHE_TTL_S, out)
    return bool(out.get("is_admin"))


def mark_ready(settings, *, company: str) -> dict:
    """Flip the gate to `completed` on admin-api, recording the company the layer names.

    Called ONLY after `state()` has said `ready`. Invalidates the local cache so the very next
    dispatch sees the new value rather than up to 15 seconds of stale refusal — the admin is
    watching a screen that is waiting for exactly this."""
    url = (getattr(settings, "admin_api_url", "") or "").strip()
    secret = settings.internal_api_secret.get_secret_value()
    if not url or not secret:
        raise RuntimeError("cannot record the gate: admin-api url/secret are not configured here")
    out = _admin_api(url, secret, "/internal/settings/global_setup", method="PUT",
                     payload={"state": COMPLETED, "company": company,
                              "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    _CACHE.clear()
    return out
