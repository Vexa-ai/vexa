"""front_page.py — THE TWO FACTS THE FRONT PAGE'S SECOND SENTENCE IS MADE OF (Vexa-ai/vexa#1634).

Founder, 2026-09-06, on the strip #1628 had just sized down to the header: *"what about this one?
never spoke about how to make it right, helpful and nice."* The strip fitted; it still read as a
list of repository facts — a commit subject, a git author id, a file count. **A person needs a
sentence about a place**, and the two things that sentence names are a THING and a PERSON:

  * *the policies wizard ask* — the changed page by its **title**, never by its path;
  * *Jane Smith* — the author by their **name**, never by an email or a subject id.

Neither is in a git log. `%s` is a commit subject, which is a sentence about a repository; `%an` is
the committing principal, which on this product is the subject id a mount commits as. So this module
is where each is RESOLVED, once, on the server that can read the files — because the alternative is
a client parsing markdown it has not fetched and inventing a name from an address.

WHAT IT REFUSES TO DO, and both refusals are the point:

  * **It never answers with an email.** Not the author's, not a member's. An address is how the
    system finds a person; it is not what a person is called, and a line that says *Changed 2 hours
    ago by jsmith@example.com* has told the reader the product does not know who works here. When
    no name can be read the answer is ``None`` and the sentence says *someone* — an honest gap beats
    a plausible-looking address.
  * **It never invents a title.** ``title:`` in the front matter, else the first ``# `` heading,
    else the file's own name with its hyphens opened out. The third is a floor rather than a guess:
    ``asks/policies-wizard.md`` has neither of the first two and *the policies wizard ask* is what
    the founder called it, which is the file's name read aloud.

WHERE A NAME COMES FROM, in order, and why that order.

  1. **The person's own page on their own desk** — ``kg/entities/person/<slug>.md`` carrying
     ``self: true``. That is where the product already puts who somebody is (``system_mounts``: *"the
     full profile … lives in the user's Personal workspace as the `self: true` person entity"*), it
     is written by the person's own agent in conversation with them, and it is therefore the one
     spelling of their name they have actually seen.
  2. **The company directory** — a person page in ``_global/kg/entities/person/`` whose front matter
     names this ``subject:`` or this ``email:``. The company layer is the tier everybody reads; a
     colleague who has never opened their own desk still has a name if somebody wrote them down.
  3. **Nothing.** Deliberately not the email's local part, not the subject id, not the git author.

READING ANOTHER PERSON'S DESK FOR ONE STRING. That is what step 1 does, and it is the narrowest
possible read: this module returns a NAME and never content, never a path, never the fact that a
page exists. The caller already shows that person's address (the roster has carried ``email`` since
memberships were stored), so a name is strictly less than what the reader can see — and a directory
whose whole job is "who is this" cannot be built out of files nobody may open.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

#: The company layer's slug — the tier the directory lives in. One spelling, shared with
#: ``system_mounts.GLOBAL_SLUG`` (imported there rather than here so this module stays importable
#: on its own, the way ``chat_intents`` is).
GLOBAL_SLUG = "_global"

#: Where a person's page lives in every workspace that has one.
PERSON_DIR = "kg/entities/person"

#: A subject id as a PATH SEGMENT. The caller's ids come from the identity edge and from a members
#: roster, so they are not attacker-controlled — but this module opens a directory named by one, and
#: a guard at the point of the join is cheaper than a proof about every caller.
_SAFE_SUBJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,127}")

#: How much of a page is read to find its title. The two things that can carry one — the front
#: matter and the first heading — are both at the top, and a title is not worth reading a megabyte.
_HEAD_BYTES = 4000

_H1 = re.compile(r"^#\s+(.+?)\s*$", re.M)


# ── the hide list, as this tier sees it ─────────────────────────────────────────────────────────
#
# The same rule the terminal's `minutes/machinery.ts` applies, in the one place a server needs it:
# what a person is SHOWN. A changed-pages sentence that counted `flows/` and `policy/` would say
# "six pages" about a commit that touched one page and five files nobody opens as a page.
#
# Kept as a short mirror rather than shared, because there is nothing to share it through — the
# lists live in a TypeScript module in another image. `test_workspace_last_change.py` pins the pair
# that matter here (`policy/`, `CLAUDE.md`) so a drift is a failing test rather than a wrong count.
MACHINERY_FILES = ("CLAUDE.md",)
MACHINERY_DIRS = ("flows", "skills", "routines", "views", "policy", "kg/templates")
#: …except in the company layer, where `flows/` is a page per flow, written for the administrator.
COMPANY_CONTENT_DIRS = ("flows",)


def is_machinery(path: str, slug: Optional[str] = None) -> bool:
    """Is this workspace-relative path machinery rather than a page a person reads?"""
    p = str(path or "").strip().strip("/")
    if not p:
        return True
    if any(seg.startswith(".") for seg in p.split("/")):
        return True
    if p in MACHINERY_FILES:
        return True
    dirs = [d for d in MACHINERY_DIRS
            if slug != GLOBAL_SLUG or d not in COMPANY_CONTENT_DIRS]
    return any(p == d or p.startswith(f"{d}/") for d in dirs)


def is_page(path: str, slug: Optional[str] = None) -> bool:
    """A markdown page a reader is shown — the unit the last-change sentence counts."""
    return bool(re.search(r"\.mdx?$", str(path or ""), re.I)) and not is_machinery(path, slug)


# ── front matter, shallowly ─────────────────────────────────────────────────────────────────────

def front_matter(text: str) -> dict[str, str]:
    """The leading ``---`` block as flat ``key: value`` pairs, or ``{}``.

    Deliberately not YAML: the three things read out of a page here — ``title``, ``self``, ``email``
    — are scalars on their own line in every file this product writes, and a parser that handled
    nested structures would be a dependency and a surface for a page to surprise this module with."""
    t = str(text or "")
    if not t.startswith("---"):
        return {}
    end = t.find("\n---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    for line in t[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, val = line.partition(":")
        if not sep or not key.strip() or key.startswith((" ", "\t", "-")):
            continue
        out[key.strip().lower()] = val.strip().strip("'\"")
    return out


def _first_h1(text: str) -> Optional[str]:
    body = str(text or "")
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4:]
    m = _H1.search(body)
    return m.group(1).strip() if m else None


def humanized(path: str) -> str:
    """A file's own name, read aloud: ``asks/policies-wizard.md`` → ``policies wizard``.

    The floor under the two real answers, and the reason it is a floor rather than a guess: it says
    exactly what the file is called, with the punctuation a filesystem needs and a sentence does
    not. `asks/policies-wizard.md` carries neither a `title:` nor a heading — it opens with the
    prompt it is — and *the policies wizard ask* is what the founder called it in this very issue."""
    stem = str(path or "").rsplit("/", 1)[-1]
    stem = re.sub(r"\.mdx?$", "", stem, flags=re.I)
    return " ".join(stem.replace("_", " ").replace("-", " ").split()) or stem


def page_title(text: Optional[str], path: str) -> str:
    """What this page is CALLED: ``title:`` → the first ``# `` heading → the file's name.

    The order is the issue's (#1634 rule 2) and each step is a thing somebody wrote on purpose."""
    fm = front_matter(text or "")
    titled = fm.get("title", "").strip()
    if titled:
        return titled
    h1 = _first_h1(text or "")
    if h1:
        return h1
    return humanized(path)


# ── who somebody is ─────────────────────────────────────────────────────────────────────────────

def _name_of_person_page(text: str) -> Optional[str]:
    """A person page's own name: ``name:`` in the front matter, else its first heading."""
    fm = front_matter(text)
    named = (fm.get("name") or "").strip()
    if named:
        return named
    h1 = _first_h1(text)
    return h1.strip() if h1 else None


def _head(p: Path) -> str:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(_HEAD_BYTES)
    except OSError:
        return ""


def _self_page_name(desk: Path) -> Optional[str]:
    """The name on the ``self: true`` person page of the desk at ``desk``."""
    folder = desk / PERSON_DIR
    if not folder.is_dir():
        return None
    try:
        pages = sorted(folder.glob("*.md"))
    except OSError:
        return None
    for page in pages:
        text = _head(page)
        if str(front_matter(text).get("self", "")).strip().lower() not in ("true", "yes", "on"):
            continue
        name = _name_of_person_page(text)
        if name:
            return name
    return None


def _directory_name(root: Path, subject: str, email: Optional[str]) -> Optional[str]:
    """The company directory's answer — a `_global` person page that names this subject or address."""
    folder = root / GLOBAL_SLUG / PERSON_DIR
    if not folder.is_dir():
        return None
    want_mail = (email or "").strip().lower()
    try:
        pages = sorted(folder.glob("*.md"))
    except OSError:
        return None
    for page in pages:
        text = _head(page)
        fm = front_matter(text)
        matches = (str(fm.get("subject", "")).strip() == str(subject)
                   or (want_mail and str(fm.get("email", "")).strip().lower() == want_mail))
        if not matches:
            continue
        name = _name_of_person_page(text)
        if name:
            return name
    return None


def looks_like_email(value: str) -> bool:
    return "@" in str(value or "")


def person_name(root: Path, subject: str, *, email: Optional[str] = None) -> Optional[str]:
    """What this person is CALLED, or ``None`` — their own page, then the directory, never an address.

    ``None`` is an answer and the caller must render it as one. Falling back to the address is the
    one thing this function exists to stop: it is what the strip did before #1634, and the line it
    produced (*Changed 14 minutes ago by 126*, or by an address) is the reason the founder said the
    strip reads as repository facts rather than as a sentence about a place."""
    sub = str(subject or "").strip()
    if not sub or not _SAFE_SUBJECT.fullmatch(sub):
        return None
    root = Path(root)
    try:
        own = _self_page_name(root / sub)
    except OSError:
        own = None
    if own and not looks_like_email(own):
        return own
    try:
        listed = _directory_name(root, sub, email)
    except OSError:
        listed = None
    return listed if listed and not looks_like_email(listed) else None


def first_name(name: Optional[str]) -> Optional[str]:
    """The first word of a name — what a sentence about a place calls the person who writes it."""
    parts = str(name or "").split()
    return parts[0] if parts else None


# ── the last change, described ──────────────────────────────────────────────────────────────────

def describe_commit(commit: dict, *, slug: Optional[str], read_page, name_of) -> dict:
    """One commit as the front page's second sentence needs it.

    ``read_page(path) -> str | None`` and ``name_of(author) -> str | None`` are passed in rather
    than reached for, so this stays testable without a repository and without a workspace volume —
    and so the ONE authorization rule that matters (which workspace this is) is decided by the
    route's ``_read_target``, above, and not a second time in here."""
    files = [f for f in (commit.get("files") or []) if is_page(f, slug)]
    pages = [{"path": f, "title": page_title(read_page(f), f)} for f in files]
    author = str(commit.get("author") or "")
    name = name_of(author)
    # A PLUMBING COMMIT ALREADY HAS A NAME. `_commit_records` classifies the seed and policy authors
    # as `system`, and those are stamped `Vexa` / `vexa-platform` — a name, not an address, and the
    # true answer to "who changed this". Anything email-shaped is dropped on the floor either way.
    if not name and commit.get("kind") == "system" and author and not looks_like_email(author):
        name = author
    return {
        "sha": commit.get("sha"),
        "msg": commit.get("msg"),
        "when": commit.get("when"),
        "ts": commit.get("ts"),
        "kind": commit.get("kind"),
        "author": name,
        "pages": pages,
        "count": len(pages),
        "files": list(commit.get("files") or []),
    }
