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

  0. **THE ADDRESS IS THE KEY, AND THE DESK IS UID-NUMBERED** (Vexa-ai/vexa#1642). Everything below
     is reached FROM an address, because an address is the only thing the callers actually hold. A
     turn commit is authored ``%an = <the sign-in address>`` / ``%ae = <subject>@vexa.local``, and
     the desk on disk is named by the SUBJECT — ``/workspaces/176`` — so an address handed straight
     to a desk lookup opens nothing. ``subject_for_address`` is the directory step: the mount
     principal's local part, else the ``policy/members.json`` row that carries both.

     This is exactly how the founder's own instance came to read *someone*: the route asked for the
     name of ``dmitry@vexa.ai``, that string was joined onto the store root as if it were a subject,
     no such directory existed, and the answer was ``None``.
  1. **The person's own page on their own desk** — ``kg/entities/person/<slug>.md`` carrying
     ``self: true``. That is where the product already puts who somebody is (``system_mounts``: *"the
     full profile … lives in the user's Personal workspace as the `self: true` person entity"*), it
     is written by the person's own agent in conversation with them, and it is therefore the one
     spelling of their name they have actually seen.
  2. **The company directory** — a person page in ``_global/kg/entities/person/`` whose front matter
     names this ``subject:`` or this ``email:``. The company layer is the tier everybody reads; a
     colleague who has never opened their own desk still has a name if somebody wrote them down.
  3. **The people record** — the ``name`` on this person's row in a workspace's
     ``policy/members.json``. It is the same roster the panel already renders, so a name written
     there is a name somebody in this company typed on purpose.
  4. **The identity note** — ``.system/<subject>/identity.md``'s ``**name:**`` bullet, which is the
     product's own light "who you're helping" reference and the one fact ``engine.py`` tells every
     agent not to leave blank. Only the name is read; never a line of it beyond that.
  5. **The address, read as a name** (``name_from_address``) — ``dmitry@vexa.ai`` → *Dmitry*. A
     floor, not a guess: it is the person's own address with the punctuation a mailbox needs and a
     sentence does not, and it is what the founder's line should have said all along.

  **There is no step for the word *someone*, and none for *the admin*** (#1642). Those were what the
  chain answered when every step above it was reached with the wrong key. A name the reader can
  check beats a pronoun that tells them the product does not know who works here; where even an
  address is missing the caller drops the clause instead — *Changed 2 hours ago* names nobody and
  claims nothing.

READING ANOTHER PERSON'S DESK FOR ONE STRING. That is what step 1 does, and it is the narrowest
possible read: this module returns a NAME and never content, never a path, never the fact that a
page exists. The caller already shows that person's address (the roster has carried ``email`` since
memberships were stored), so a name is strictly less than what the reader can see — and a directory
whose whole job is "who is this" cannot be built out of files nobody may open. Step 4 reads one
bullet of one file in the private tier under the same rule and for the same reason: a display name
is not a chat, a session or a setting, which is what `POLICIES.md` keeps `_system` private for.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

#: The company layer's slug — the tier the directory lives in. One spelling, shared with
#: ``system_mounts.GLOBAL_SLUG`` (imported there rather than here so this module stays importable
#: on its own, the way ``chat_intents`` is).
GLOBAL_SLUG = "_global"

#: Where a person's page lives in every workspace that has one.
PERSON_DIR = "kg/entities/person"

#: The workspace's own roster — the "people record" of step 3, and the directory of step 0.
MEMBERS_FILE = "policy/members.json"

#: Where the private tier lives on the store (``system_mounts.SYSTEM_STORE_DIRNAME``, spelled here
#: so this module stays importable on its own).
SYSTEM_STORE_DIRNAME = ".system"

#: The domain a MOUNT commits under: ``%ae`` on a turn commit is ``<subject>@vexa.local`` (D4, and
#: ``dispatch.py``'s principal). It is the one address shape that IS a subject, which is what makes
#: it the first and cheapest step of the directory.
MOUNT_DOMAIN = "vexa.local"

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
    """A person page's own name: ``name:``, else ``title:``, else its first heading.

    ``title:`` is in that list because it is what the KG entity template actually writes — the
    founder's own ``kg/entities/person/dmitry.md`` opens ``type: person / id: dmitry / title:
    Dmitry`` and carries no ``name:`` at all. A resolver that knows only the key the schema does not
    use is a resolver that answers ``None`` on the one instance where the name certainly exists."""
    fm = front_matter(text)
    for key in ("name", "title"):
        named = (fm.get(key) or "").strip()
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


def _self_page_name(desk: Path, address: Optional[str] = None) -> Optional[str]:
    """The name on the OWNER's own person page on the desk at ``desk``.

    ``self: true`` first — that is the marker the product documents and the one the issue names.
    Then, when an address is known, the page that IS that person by their own front matter: a
    ``kg/entities/person/`` page whose ``email:`` is that address or whose ``id:``/file name is its
    local part. That second pass is not a widening of who may be named — it is still only this one
    desk, which belongs to one person — and it is what answers on the founder's own desk, where
    ``dmitry.md`` says ``type: person / id: dmitry / title: Dmitry`` and carries no ``self:`` key at
    all. A marker that has to be there for a name to resolve is a marker whose absence silently
    renames somebody *someone*."""
    folder = desk / PERSON_DIR
    if not folder.is_dir():
        return None
    try:
        pages = sorted(folder.glob("*.md"))
    except OSError:
        return None
    heads = [(page, _head(page)) for page in pages]
    for _page, text in heads:
        if str(front_matter(text).get("self", "")).strip().lower() not in ("true", "yes", "on"):
            continue
        name = _name_of_person_page(text)
        if name:
            return name
    want_mail = str(address or "").strip().lower()
    want_id = local_part(address).lower()
    if not want_mail and not want_id:
        return None
    for page, text in heads:
        fm = front_matter(text)
        mine = ((want_mail and str(fm.get("email", "")).strip().lower() == want_mail)
                or (want_id and str(fm.get("id", "")).strip().lower() == want_id)
                or (want_id and re.sub(r"\.mdx?$", "", page.name, flags=re.I).lower() == want_id))
        if not mine:
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


# ── the directory: an address is the key, a subject is what opens a desk (#1642) ─────────────────

def local_part(address: Optional[str]) -> str:
    """Everything before the ``@``, with a ``+tag`` dropped. ``""`` when there is nothing."""
    text = str(address or "").strip()
    if not text:
        return ""
    return text.split("@", 1)[0].split("+", 1)[0].strip()


def _rosters(root: Path) -> list[list[dict]]:
    """Every workspace's ``policy/members.json``, as parsed lists. Failures are simply absent."""
    out: list[list[dict]] = []
    try:
        entries = sorted(p for p in Path(root).iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return out
    for ws in entries:
        try:
            raw = (ws / MEMBERS_FILE).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            rows = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(rows, list):
            out.append([r for r in rows if isinstance(r, dict)])
    return out


def subject_for_address(root: Path, address: Optional[str]) -> Optional[str]:
    """ADDRESS → USER, so a desk can be opened for it (#1642's directory step).

    Two answers, both read rather than guessed:

      * ``<subject>@vexa.local`` IS a subject — the principal every mount commits as (D4). This is
        the case that matters most, because it is the ``%ae`` on every turn commit in the store;
      * otherwise the ``policy/members.json`` row whose ``email`` matches. That file is the one
        place this product writes an address and a subject on the same line, which makes it the
        directory whether or not anything else ever calls it one.

    ``None`` when neither answers, and ``None`` here is not a failure — the chain above simply
    continues from the address itself."""
    addr = str(address or "").strip()
    if not addr:
        return None
    domain = addr.split("@", 1)[1].strip().lower() if "@" in addr else ""
    if domain == MOUNT_DOMAIN:
        sub = local_part(addr)
        return sub if sub and _SAFE_SUBJECT.fullmatch(sub) else None
    want = addr.lower()
    for rows in _rosters(Path(root)):
        for row in rows:
            if str(row.get("email") or "").strip().lower() != want:
                continue
            sub = str(row.get("subject") or "").strip()
            if sub and _SAFE_SUBJECT.fullmatch(sub):
                return sub
    return None


def _roster_name(root: Path, subject: Optional[str], email: Optional[str]) -> Optional[str]:
    """THE PEOPLE RECORD's own answer — a ``name`` written beside this person on a roster row."""
    want_sub = str(subject or "").strip()
    want_mail = str(email or "").strip().lower()
    for rows in _rosters(Path(root)):
        for row in rows:
            same = ((want_sub and str(row.get("subject") or "").strip() == want_sub)
                    or (want_mail and str(row.get("email") or "").strip().lower() == want_mail))
            if not same:
                continue
            name = str(row.get("name") or "").strip()
            if name and not looks_like_email(name):
                return name
    return None


#: The identity note's own bullet — ``- **name:** Dmitry (dmitry@vexa.ai)`` (``_IDENTITY_STUB``).
_IDENTITY_NAME = re.compile(r"^\s*[-*]\s*\*\*name:?\*\*\s*(.+?)\s*$", re.M | re.I)
#: …and what the STUB says while nobody has answered it. Never a name.
_UNWRITTEN = ("unknown", "n/a", "tbd", "the user", "_(unknown", "(unknown")


def identity_name(root: Path, subject: Optional[str]) -> Optional[str]:
    """THE IDENTITY NOTE's answer — ``.system/<subject>/identity.md``'s ``**name:**`` bullet.

    One line of one file, and nothing else is read out of the private tier. The stub's own
    placeholder (*(unknown — ask the user…)*) is refused: it is the question, not the answer, and
    printing it on a front page would be the product asking a stranger who its own user is."""
    sub = str(subject or "").strip()
    if not sub or not _SAFE_SUBJECT.fullmatch(sub):
        return None
    note = Path(root) / SYSTEM_STORE_DIRNAME / sub / "identity.md"
    m = _IDENTITY_NAME.search(_head(note))
    if not m:
        return None
    # `Dmitry (dmitry@vexa.ai)` — the address in the bullet is a parenthesis, never the name.
    name = re.sub(r"\s*\([^)]*\)\s*$", "", m.group(1)).strip().strip("*_`").strip()
    if not name or looks_like_email(name):
        return None
    return None if name.lower().startswith(_UNWRITTEN) else name


def name_from_address(address: Optional[str]) -> Optional[str]:
    """THE FLOOR (#1642): the address's local part, read as a name. ``dmitry@vexa.ai`` → *Dmitry*.

    It is the last step and it is deliberately not the word *someone*. An address is how a person
    signed in, so its local part is a string they chose and recognise; *someone* is a string nobody
    chose, on a line whose whole job is to say who did something.

    ``None`` where there is no name to read rather than a bad one, and there are two such cases:

      * **it must BE an address.** A git ``%an`` with no ``@`` is a git author name, and on this
        product that is usually a subject id (``176``, ``u_live``). Reading one aloud would put an
        internal id on the front page dressed as a person — the same class of mistake as the
        address it replaces;
      * **a bare id is not a name.** ``176@vexa.local`` is the principal a mount commits as, so its
        local part is a number and the answer is nothing rather than *176*."""
    if "@" not in str(address or ""):
        return None
    local = local_part(address)
    words = [w for w in re.split(r"[._\-\s]+", local) if w and not w.isdigit()]
    if not words:
        return None
    return " ".join(w[:1].upper() + w[1:] for w in words)


def person_name(root: Path, subject: Optional[str] = None, *, email: Optional[str] = None,
                address: Optional[str] = None, principal: Optional[str] = None) -> Optional[str]:
    """What this person is CALLED, or ``None`` — steps 1 to 4, never an address read verbatim.

    ``subject`` may be absent: ``principal`` (a commit's ``%ae``) and ``address`` (their sign-in
    address) are resolved to one through ``subject_for_address`` first, which is the fix #1642 is
    about. ``None`` is still an answer, and the ONE caller who must not render it as *someone* asks
    ``display_name`` instead."""
    root = Path(root)
    sub = str(subject or "").strip()
    if not sub or not _SAFE_SUBJECT.fullmatch(sub):
        sub = ""
    for candidate in (principal, address, email):
        if sub:
            break
        sub = subject_for_address(root, candidate) or ""
    mail = next((str(a).strip() for a in (email, address, principal)
                 if a and looks_like_email(str(a)) and not str(a).strip().lower().endswith(f"@{MOUNT_DOMAIN}")), None)
    if sub:
        try:
            own = _self_page_name(root / sub, mail)
        except OSError:
            own = None
        if own and not looks_like_email(own):
            return own
    try:
        listed = _directory_name(root, sub, mail) if (sub or mail) else None
    except OSError:
        listed = None
    if listed and not looks_like_email(listed):
        return listed
    try:
        rostered = _roster_name(root, sub, mail)
    except OSError:
        rostered = None
    if rostered:
        return rostered
    try:
        return identity_name(root, sub) if sub else None
    except OSError:
        return None


def display_name(root: Path, subject: Optional[str] = None, *, email: Optional[str] = None,
                 address: Optional[str] = None, principal: Optional[str] = None) -> Optional[str]:
    """The whole chain, floor included — what a SENTENCE puts where a person's name goes (#1642).

    ``person_name`` answers ``None`` when nobody has written this person down; this adds the last
    step the founder's line was missing, and it is the only function on this module that will ever
    turn an address into words."""
    found = person_name(root, subject, email=email, address=address, principal=principal)
    if found:
        return found
    for candidate in (address, email):
        read = name_from_address(candidate)
        if read:
            return read
    return None


def first_name(name: Optional[str]) -> Optional[str]:
    """The first word of a name — what a sentence about a place calls the person who writes it."""
    parts = str(name or "").split()
    return parts[0] if parts else None


# ── the last change, described ──────────────────────────────────────────────────────────────────

def describe_commit(commit: dict, *, slug: Optional[str], read_page, name_of) -> dict:
    """One commit as the front page's second sentence needs it.

    ``read_page(path) -> str | None`` and ``name_of(author, email) -> str | None`` are passed in
    rather than reached for, so this stays testable without a repository and without a workspace
    volume — and so the ONE authorization rule that matters (which workspace this is) is decided by
    the route's ``_read_target``, above, and not a second time in here.

    ``name_of`` takes BOTH halves of the git author because neither alone is enough (#1642): ``%an``
    is the person's sign-in address and ``%ae`` is ``<subject>@vexa.local``, so the desk is found
    through the second and the name is read off the first."""
    files = [f for f in (commit.get("files") or []) if is_page(f, slug)]
    pages = [{"path": f, "title": page_title(read_page(f), f)} for f in files]
    author = str(commit.get("author") or "")
    name = name_of(author, str(commit.get("email") or ""))
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


# ── who writes the company layer ────────────────────────────────────────────────────────────────

#: Authors that are the PLATFORM writing, not a person accepting: the seed and policy plumbing
#: (`_commit_records` already calls these `system`) plus the terminal's own page editor, which
#: commits as itself whoever is typing.
_NOT_A_PERSON = {"platform@vexa.ai", "agent@vexa", "agent@vexa.ai", "terminal@vexa.local"}


def admin_principal(commits: list[dict]) -> tuple[Optional[str], Optional[str]]:
    """THE COMPANY LAYER'S WRITER, out of its own history — ``(%an, %ae)`` or ``(None, None)``.

    `_global/STRUCTURE.md` states the rule this reads: *"every acceptance is a commit authored by
    the administrator who made it"* (``global_layer.commit_global``). So the newest commit there
    that a PERSON made names the person who writes the company layer, and no second store, no new
    credential and no hop to another service is needed to answer *who is the admin*.

    The caller gates this on ``global_admin_only`` being on, and that gate is the whole argument:
    with it on the layer has exactly one writer by policy, so "the newest author" and "the admin"
    are the same person. With it off they are not, and the sentence does not name a writer anyway."""
    for c in commits or []:
        if str(c.get("kind") or "") == "system":
            continue
        email = str(c.get("email") or "").strip()
        if email.lower() in _NOT_A_PERSON:
            continue
        author = str(c.get("author") or "").strip()
        if not author and not email:
            continue
        return (author or None, email or None)
    return (None, None)
