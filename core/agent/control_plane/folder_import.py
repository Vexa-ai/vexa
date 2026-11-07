"""folder_import.py — a folder from a person's computer becomes a workspace (founder, 2026-10-13: "as
we drop calls we can drop folder").

The browser walks the dropped folder and sends a MANIFEST (relative paths and sizes, plus the text of
every `.gitignore` in it). This module decides, once and only here, what of that folder is imported:

    always skipped     `.git` internals, dependency and build folders (`node_modules`, `.venv`,
                       `__pycache__`, `.next`, …), operating-system litter (`.DS_Store`, …)
    never imported     `.env` and every `.env.*`, private keys and credential files — by NAME before
                       a byte is sent, and again by CONTENT when the bytes arrive (`scan_secret`)
    honoured           the folder's own `.gitignore` files, nested ones included, git's rules
    capped             total size, file count, and a per-file cap, each a key in `_global/POLICIES.md`

The browser shows the person what the server answered — it never runs a second copy of these rules,
so the sheet and the import cannot disagree. The upload then arrives in batches, each re-checked
against the stored plan, and `finish` writes the accepted files as ONE commit authored by the person.

THE POLICY KEYS, typed, each with its default (the seed carries exactly these values):

    folder_import_max_mb       200     the whole import, accepted files together
    folder_import_max_files    10000   how many files one import may carry
    folder_import_file_max_mb  25      any one file; larger ones are listed and left out

LOUD, NOT SOFT, like the connection keys: a value this module cannot read refuses the import naming
the key, because a misspelt cap that silently fell back would be a limit nobody set.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets as _secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

from control_plane import front_page
from control_plane.path_confine import within
from shared.atomic_json import write_json_atomic
from workspaces.shared import workspace_paths as wpaths

POLICIES_FILE = "POLICIES.md"

DEFAULTS: dict = {
    "folder_import_max_mb": 200,
    "folder_import_max_files": 10000,
    "folder_import_file_max_mb": 25,
}
FAMILY = "folder_import_"
#: The ceiling an administrator may raise each key to. The gateway buffers every request, so the
#: upload arrives in batches either way; these keep one import from filling a disk.
CEILINGS = {"folder_import_max_mb": 2048, "folder_import_max_files": 100000, "folder_import_file_max_mb": 200}

#: Folders whose contents are never a person's work: version-control internals, installed
#: dependencies, virtual environments and build caches. Matched on any path segment.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", ".nox", ".next", ".nuxt", ".turbo", ".parcel-cache",
    ".cache", ".gradle", ".terraform", ".vexa",
})
#: Files the operating system leaves behind.
SKIP_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r", ".localized"})
SKIP_SUFFIXES = (".pyc", ".pyo")

#: Names that hold credentials. Never imported, and the person is told so.
_SECRET_NAMES = frozenset({".netrc", ".pgpass", ".npmrc", ".pypirc", "id_rsa", "id_dsa", "id_ecdsa",
                           "id_ed25519", "credentials", ".htpasswd"})
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
#: Content that is unmistakably a credential. High precision on purpose: a false hit costs one file
#: the person can add by hand; a miss puts a key into a repository.
_SECRET_CONTENT = re.compile(
    rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"
    rb"|\bAKIA[0-9A-Z]{16}\b"
    rb"|\bgh[pousr]_[A-Za-z0-9]{36,}\b"
    rb"|\bsk_live_[A-Za-z0-9]{16,}\b"
)

REASON_GIT = "git internals"
REASON_DEPS = "dependency or build folder"
REASON_SYSTEM = "system file"
REASON_ENV = ".env file: never imported"
REASON_SECRET_NAME = "key or credential file: never imported"
REASON_SECRET_CONTENT = "contains what looks like a private key or token: never imported"
REASON_GITIGNORE = "ignored by .gitignore"
REASON_PATH = "not a usable path"
REASON_DUPLICATE = "listed twice"
REASON_NOT_RECEIVED = "not received"

#: How many skipped entries a plan returns by name; beyond that only the per-reason counts.
SKIPPED_LISTED = 500
#: A manifest longer than this is refused before any rule runs.
MAX_MANIFEST = 200000
MAX_GITIGNORE_BYTES = 256 * 1024


class PolicyUnreadable(Exception):
    def __init__(self, key: str, why: str):
        super().__init__(f"{key}: {why}")
        self.key = key
        self.why = why


def resolve(raw: Optional[str]) -> dict:
    """The administrator's typed answers (whole numbers). Absent or empty = the default."""
    attrs = front_page.front_matter(raw or "")
    for key in attrs:
        if key.startswith(FAMILY) and key not in DEFAULTS:
            raise PolicyUnreadable(key, "is not a folder import key; use " + ", ".join(DEFAULTS))
    out = {}
    for key, default in DEFAULTS.items():
        value = str(attrs.get(key, "")).strip()
        if not value:
            out[key] = default
            continue
        if not value.isdigit() or not 0 < int(value) <= CEILINGS[key]:
            raise PolicyUnreadable(key, f"{value!r} is not a whole number from 1 to {CEILINGS[key]}")
        out[key] = int(value)
    return out


def read_limits(global_dir: Optional[Path]) -> dict:
    """The caps as `_global/POLICIES.md` holds them now, read per request."""
    raw = None
    if global_dir is not None and Path(global_dir).is_dir():
        raw = wpaths.read_text_inside(global_dir, POLICIES_FILE, max_bytes=1_000_000)
    return resolve(raw)


def limits_view(limits: dict) -> dict:
    return {"max_bytes": limits["folder_import_max_mb"] * 1024 * 1024,
            "max_files": limits["folder_import_max_files"],
            "file_max_bytes": limits["folder_import_file_max_mb"] * 1024 * 1024}


# ── .gitignore, git's rules ──────────────────────────────────────────────────────────────────────

def _glob_re(pat: str) -> str:
    out, i, n = [], 0, len(pat)
    while i < n:
        if pat.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3
        elif pat.startswith("/**", i) and i + 3 == n:
            out.append("/.*"); i += 3
        elif pat.startswith("**", i):
            out.append(".*"); i += 2
        elif pat[i] == "*":
            out.append("[^/]*"); i += 1
        elif pat[i] == "?":
            out.append("[^/]"); i += 1
        elif pat[i] == "[":
            j = pat.find("]", i + 2 if pat[i + 1:i + 2] in ("!", "^", "]") else i + 1)
            if j < 0:
                out.append(re.escape("[")); i += 1
            else:
                body = pat[i + 1:j]
                if body[:1] in ("!", "^"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]"); i = j + 1
        elif pat[i] == "\\" and i + 1 < n:
            out.append(re.escape(pat[i + 1])); i += 2
        else:
            out.append(re.escape(pat[i])); i += 1
    return "".join(out)


@dataclass
class _Rule:
    base: str          # the directory the .gitignore sits in ("" = the folder's root)
    regex: "re.Pattern"
    negate: bool
    dir_only: bool
    basename: bool     # no slash in the pattern: matches the last segment at any depth


def parse_gitignore(base: str, text: str) -> list:
    rules = []
    for line in (text or "").splitlines():
        line = line.rstrip("\r")
        if not line.strip() or line.startswith("#"):
            continue
        if not line.endswith("\\ "):
            line = line.rstrip(" ")
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        if line.startswith("\\"):
            line = line[1:] if line[1:2] in ("#", "!") else line
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        anchored = "/" in line
        line = line.lstrip("/")
        try:
            regex = re.compile(_glob_re(line) + r"\Z", re.DOTALL)
        except re.error:
            continue
        rules.append(_Rule(base, regex, negate, dir_only, not anchored))
    return rules


class Gitignore:
    """Every `.gitignore` of one folder, applied as git applies them: a deeper file overrides a
    shallower one, the last matching line wins, and nothing under an ignored directory comes back."""

    def __init__(self, files: "dict[str, str]"):
        by_dir: dict = {}
        for path, text in (files or {}).items():
            p = PurePosixPath(str(path).strip("/"))
            if p.name != ".gitignore":
                continue
            base = "" if str(p.parent) == "." else str(p.parent)
            by_dir[base] = parse_gitignore(base, text)
        self._rules = by_dir
        self._dir_cache: dict = {}

    def _match(self, rel: str, is_dir: bool) -> Optional[bool]:
        verdict = None
        parts = rel.split("/")
        for depth in range(len(parts)):
            base = "/".join(parts[:depth])
            for rule in self._rules.get(base, ()):
                if rule.dir_only and not is_dir:
                    continue
                sub = "/".join(parts[depth:])
                target = parts[-1] if rule.basename else sub
                if rule.regex.match(target):
                    verdict = not rule.negate
        return verdict

    def _dir_ignored(self, d: str) -> bool:
        if d in self._dir_cache:
            return self._dir_cache[d]
        parent = d.rsplit("/", 1)[0] if "/" in d else ""
        out = bool(parent and self._dir_ignored(parent)) or bool(self._match(d, True))
        self._dir_cache[d] = out
        return out

    def ignored(self, rel: str) -> bool:
        if not self._rules:
            return False
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if parent and self._dir_ignored(parent):
            return True
        return bool(self._match(rel, False))


# ── the plan ─────────────────────────────────────────────────────────────────────────────────────

def is_env_name(name: str) -> bool:
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def is_secret_name(name: str) -> bool:
    low = name.lower()
    stem = low.split(".", 1)[0] if not low.startswith(".") else low
    return low in _SECRET_NAMES or stem in _SECRET_NAMES or low.endswith(_SECRET_SUFFIXES)


def skip_reason(rel: str) -> Optional[str]:
    """Why ``rel`` is never imported, whatever the folder's own rules say — or None."""
    raw = str(rel or "").replace("\\", "/")
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts:
        return REASON_PATH
    if parts[0] == ".git" or ".git" in parts[:-1]:
        return REASON_GIT
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return REASON_DEPS
    name = parts[-1]
    if name in SKIP_FILES or name.endswith(SKIP_SUFFIXES) or name.startswith("._"):
        return REASON_SYSTEM
    if is_env_name(name):
        return REASON_ENV
    if is_secret_name(name):
        return REASON_SECRET_NAME
    try:
        wpaths.relative_parts(raw)
    except wpaths.PathRefused:
        return REASON_GIT if name == ".git" else REASON_PATH
    return None


def scan_secret(content: bytes) -> Optional[str]:
    return REASON_SECRET_CONTENT if _SECRET_CONTENT.search(content or b"") else None


def _norm(rel: str) -> str:
    return "/".join(p for p in str(rel).replace("\\", "/").split("/") if p and p != ".")


@dataclass
class Plan:
    accepted: list = field(default_factory=list)      # [{path, size}]
    skipped: list = field(default_factory=list)       # [{path, reason}]
    counts: dict = field(default_factory=dict)        # reason -> n
    total_bytes: int = 0
    blocked: Optional[str] = None

    def skip(self, path: str, reason: str, n: int = 1) -> None:
        self.counts[reason] = self.counts.get(reason, 0) + n
        if len(self.skipped) < SKIPPED_LISTED:
            self.skipped.append({"path": path, "reason": reason})


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.0f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


def build_plan(files: Iterable[dict], gitignores: "dict[str, str]", limits: dict,
               pruned: Iterable[str] = ()) -> Plan:
    """What of a dropped folder is imported. ``pruned`` are folders the browser did not walk
    because their name is in `SKIP_DIRS`; they are reported, never trusted beyond that."""
    lv = limits_view(limits)
    ig = Gitignore({k: v for k, v in (gitignores or {}).items()
                    if isinstance(v, str) and len(v) <= MAX_GITIGNORE_BYTES})
    plan = Plan()
    for d in pruned or ():
        d = _norm(d)
        if d and d.rsplit("/", 1)[-1] in SKIP_DIRS:
            plan.skip(d + "/", REASON_GIT if d.endswith(".git") else REASON_DEPS)
    seen: set = set()
    for entry in files:
        rel = _norm(entry.get("path", ""))
        try:
            size = int(entry.get("size", 0))
        except (TypeError, ValueError):
            size = -1
        reason = skip_reason(rel) if size >= 0 else REASON_PATH
        if reason is None and rel in seen:
            reason = REASON_DUPLICATE
        if reason is None and ig.ignored(rel):
            reason = REASON_GITIGNORE
        if reason is None and size > lv["file_max_bytes"]:
            reason = f"larger than {_mb(lv['file_max_bytes'])} ({_mb(size)})"
        if reason:
            plan.skip(rel or str(entry.get("path", "")), reason)
            continue
        seen.add(rel)
        plan.accepted.append({"path": rel, "size": size})
        plan.total_bytes += size
    if len(plan.accepted) > lv["max_files"]:
        plan.blocked = (f"{len(plan.accepted)} files to import; the limit is {lv['max_files']}. "
                        "Add a .gitignore for what you do not need, or drop a smaller folder.")
    elif plan.total_bytes > lv["max_bytes"]:
        plan.blocked = (f"{_mb(plan.total_bytes)} to import; the limit is {_mb(lv['max_bytes'])}. "
                        "Add a .gitignore for what you do not need, or drop a smaller folder.")
    elif not plan.accepted:
        plan.blocked = "Nothing in this folder can be imported."
    return plan


# ── the stored plan and its staged bytes ─────────────────────────────────────────────────────────

_ID = re.compile(r"[a-f0-9]{32}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return _secrets.token_hex(16)


def valid_id(plan_id: str) -> bool:
    return bool(_ID.fullmatch(str(plan_id or "")))


def store_dir(root: "str | Path", subject: str) -> Path:
    """`<root>/.imports/<subject>/folders` — one person's imports, never another's."""
    if not wpaths.is_workspace_name(subject, tier=False):
        raise ValueError("invalid subject")
    path = within(root, f".imports/{subject}/folders", ValueError("invalid subject"))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def save(root, subject: str, record: dict) -> None:
    record["updated_at"] = _now()
    write_json_atomic(store_dir(root, subject) / f"{record['plan_id']}.json", record)


def load(root, subject: str, plan_id: str) -> Optional[dict]:
    if not valid_id(plan_id):
        return None
    path = within(store_dir(root, subject), f"{plan_id}.json", ValueError("invalid plan id"))
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def staging(root, subject: str, plan_id: str) -> Path:
    path = within(store_dir(root, subject), f"{plan_id}.files", ValueError("invalid plan id"))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def drop_staging(root, subject: str, plan_id: str) -> None:
    path = within(store_dir(root, subject), f"{plan_id}.files", ValueError("invalid plan id"))
    if path.exists():
        wpaths.remove_tree(path, ignore_errors=True)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def safe_folder_name(name: str) -> str:
    """The sub-folder an import INTO an existing workspace lands in: the dropped folder's name as one
    path segment, or `imported`."""
    seg = re.sub(r"[\x00-\x1f/\\]", "", str(name or "")).strip().strip(".")[:80].strip()
    if not seg or seg in SKIP_DIRS:
        return "imported"
    try:
        wpaths.relative_parts(seg)
    except wpaths.PathRefused:
        return "imported"
    return seg
