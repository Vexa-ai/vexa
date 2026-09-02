"""entities.py — the ONE write that turns something learned into a page in the knowledge graph.

PRD decision 24 (founder, 2026-09-02): *"how to update the agent so that it updates entities
whenever there is a chance for that? so it does not hesitate creating pages"*. Hesitation was never
a prompt problem — it was a **cost** problem. Recording one fact about a person meant: guess whether
a page exists, read it, invent a shape, merge without clobbering, remember the wikilink rule, and
commit. Six decisions for one sentence, so the agent skipped it and wrote prose instead. This module
collapses all six into one call that is safe to make on a maybe.

Deliberately a PURE FUNCTION OVER A DIRECTORY: no HTTP, no docker, no git required, so it is
offline-provable and the same code serves the control-plane endpoint, the MCP tool that forwards to
it, and the tests. Committing is a separate, optional step (``commit_entity``) — the caller owns the
repo, and a caller mid-turn on a workspace another writer owns must be able to write without
committing at all.

⚠ FRONTMATTER VOCABULARY. The tool's ARGUMENTS are the founder's words — ``kind`` and ``name`` — but
the KEYS written to the page are the ones this workspace's readers already use: ``type``, ``id``,
``title`` (``behavior/workspaces/default/kg/templates/*.md``, the terminal's wikilink resolver, the
per-type ``index.md`` files). Writing ``kind:``/``name:`` instead would have produced pages that look
right and are invisible to every existing reader — the exact defect ``workspace_write``'s CONFIG_VOCAB
guard exists to refuse. ``aliases``/``created``/``sources`` are added on top; nothing is renamed.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import unicodedata
from pathlib import Path

# The five kinds decision 24 names. A kind outside this set is refused rather than guessed into a new
# directory: a directory nothing indexes is a page nobody will ever find again.
KINDS = ("person", "company", "meeting", "project", "decision")

ENTITIES_DIR = "kg/entities"
INDEX_PATH = "kg/INDEX.md"
MISSING_PATH = "kg/MISSING.md"

_FRONTMATTER = re.compile(r"^---\n([\s\S]*?)\n---\n?")
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_DATED_HEADING = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$", re.M)
_BULLET = re.compile(r"^\s*[-*]\s+(.+?)\s*$", re.M)

# The index is mounted into EVERY dispatch, so it is a prompt-budget line item, not a report.
INDEX_MAX_ROWS = 400


class EntityRefused(ValueError):
    """A write this module will not perform, carrying the reason the CALLER has to act on.

    Refusals are the point of the interface, not its edge: "a page carries only what was said or
    read, with its source" (decision 24.5) is enforceable only if a sourceless fact cannot be
    written at all. A rule that is merely asked for is a rule the model obeys about half the time —
    the grounding gate measured exactly that."""


def slugify(name: str) -> str:
    """kebab-case ascii, the shape the templates promise (``id: <slug>`` matches the filename)."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80]


def entity_rel_path(kind: str, name: str) -> str:
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise EntityRefused(f"{kind!r} is not an entity kind — one of {', '.join(KINDS)}")
    slug = slugify(name)
    if not slug:
        raise EntityRefused("an entity needs a name that survives slugification")
    return f"{ENTITIES_DIR}/{kind}/{slug}.md"


def split_frontmatter(text: str) -> tuple[list[str], str]:
    """``(frontmatter lines, body)``. Lines are kept RAW — comments, ordering, keys this module has
    never heard of. A page a human edited must come back out the way they left it."""
    m = _FRONTMATTER.match(text or "")
    if not m:
        return [], text or ""
    return m.group(1).splitlines(), text[m.end():]


def _fm_get(lines: list[str], key: str) -> str | None:
    for ln in lines:
        k, sep, v = ln.partition(":")
        if sep and k.strip() == key:
            return v.split("#")[0].strip()
    return None


def _fm_set(lines: list[str], key: str, value: str) -> list[str]:
    out, done = [], False
    for ln in lines:
        k, sep, _ = ln.partition(":")
        if sep and k.strip() == key and not done:
            out.append(f"{key}: {value}")
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"{key}: {value}")
    return out


def _list_field(raw: str | None) -> list[str]:
    raw = (raw or "").strip()
    if not raw or raw in ("[]", "~", "null"):
        return []
    return [p.strip() for p in raw.strip("[]").split(",") if p.strip()]


def _render_list(items) -> str:
    seen, out = set(), []
    for i in items:
        i = str(i).strip()
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return "[" + ", ".join(out) + "]"


def _normalise(fact: str) -> str:
    """What "the same fact" means for idempotency: same words, ignoring case, punctuation and the
    wikilink brackets. Without dropping the brackets, a fact re-stated after its subject got a page
    would append a near-duplicate line, and the page grows by a paragraph per turn forever."""
    s = _WIKILINK.sub(r"\1", str(fact or ""))
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return " ".join(s.split())


# Every recorded fact ends `- <fact> - source: <where it came from>`, so the stored line and the
# fact the caller passes are never byte-identical. Comparing them raw made "idempotent on identical facts"
# quietly false: the same sentence, re-stated next turn, appended again with a second source. Strip
# the attribution before comparing — the fact is the claim, not the provenance of this telling.
_SOURCE_SUFFIX = re.compile(r"\s+—\s+source:\s.*$")


def existing_facts(text: str) -> set[str]:
    out = set()
    for b in _BULLET.findall(text or ""):
        n = _normalise(_SOURCE_SUFFIX.sub("", b))
        if n:
            out.add(n)
    return out


def wikilinks(texts) -> list[str]:
    out, seen = [], set()
    for t in texts:
        for name in _WIKILINK.findall(str(t or "")):
            n = name.strip()
            if n and n.lower() not in seen:
                seen.add(n.lower())
                out.append(n)
    return out


def resolve_links(root, names) -> tuple[list[str], list[str]]:
    """``(resolved, unresolved)`` — which ``[[Name]]`` already have a page anywhere under
    ``kg/entities/``. Unresolved names are RETURNED, never auto-created: a page minted from a name
    with no facts behind it is the invention decision 24.5 forbids, and the kg-links rule already
    says a wikilink with no page renders as an inert "not found" chip. The caller upserts them with
    their own source; the tool result says so in words."""
    base = Path(root) / ENTITIES_DIR
    resolved, unresolved = [], []
    for n in names:
        slug = slugify(n)
        hit = any((base / k / f"{slug}.md").exists() for k in KINDS) if slug else False
        (resolved if hit else unresolved).append(n)
    return resolved, unresolved


def _today(today: str | None) -> str:
    return today or _dt.date.today().isoformat()


def upsert_entity(root, kind: str, name: str, facts, source: str, *,
                  today: str | None = None, aliases=()) -> dict:
    """Create ``kg/entities/<kind>/<slug>.md`` with frontmatter and a first dated entry, or append a
    dated entry to the page already there. Returns what happened, in the caller's words.

    Idempotent on identical facts: a fact already on the page (by ``_normalise``) is dropped, and a
    call whose every fact is already there writes nothing and reports ``changed: False``. That is
    what makes the forced write-back phase safe to run on EVERY turn — a turn that learned nothing
    new costs one no-op, not a duplicated paragraph."""
    root = Path(root)
    src = str(source or "").strip()
    facts = [str(f).strip() for f in (facts or []) if str(f).strip()]
    if not facts:
        raise EntityRefused("nothing to record — pass the facts this turn learned, or say nothing new")
    if not src:
        raise EntityRefused(
            "every fact needs a source: what was said or read that this came from (the meeting, the "
            "mail, the file, the person's own words). A page carries only what was said or read — "
            "if you do not have a source the gap goes to kg/MISSING.md, never onto the page.")

    rel = entity_rel_path(kind, name)
    path = root / rel
    existed = path.exists()
    raw = path.read_text(encoding="utf-8", errors="replace") if existed else ""
    fm, body = split_frontmatter(raw)

    fresh = [f for f in facts if _normalise(f) not in existing_facts(raw)]
    linked = wikilinks(facts)
    resolved, unresolved = resolve_links(root, linked)
    day = _today(today)

    if existed and not fresh:
        return {"path": rel, "created": False, "changed": False, "facts_written": 0,
                "already_recorded": len(facts), "links_resolved": resolved,
                "links_missing": unresolved, "kind": kind, "name": name}

    if not existed:
        fm = [f"type: {kind}", f"id: {slugify(name)}", f"title: {name}",
              f"aliases: {_render_list(aliases)}", f"created: {day}",
              f"sources: {_render_list([src])}"]
        body = f"\n# {name}\n"
    else:
        # A page that predates this module (or one a human wrote) may carry none of these keys.
        # Fill what is missing; never overwrite a title or a created date somebody else set.
        if _fm_get(fm, "type") is None:
            fm = _fm_set(fm, "type", kind)
        if _fm_get(fm, "id") is None:
            fm = _fm_set(fm, "id", slugify(name))
        if _fm_get(fm, "title") is None:
            fm = _fm_set(fm, "title", name)
        if _fm_get(fm, "created") is None:
            fm = _fm_set(fm, "created", day)
        fm = _fm_set(fm, "aliases", _render_list(_list_field(_fm_get(fm, "aliases")) + list(aliases)))
        fm = _fm_set(fm, "sources", _render_list(_list_field(_fm_get(fm, "sources")) + [src]))

    written = fresh or facts
    entry = [f"\n## {day}\n"] + [f"- {f} — source: {src}" for f in written] + [""]
    body = body.rstrip("\n") + "\n" + "\n".join(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + "\n".join(fm) + "\n---\n" + body.lstrip("\n"), encoding="utf-8")
    return {"path": rel, "created": not existed, "changed": True, "facts_written": len(written),
            "already_recorded": len(facts) - len(written), "links_resolved": resolved,
            "links_missing": unresolved, "kind": kind, "name": name, "date": day}


# ── the index that rides in every dispatch ───────────────────────────────────────────────────────

def _last_updated(text: str, fallback: str) -> str:
    days = _DATED_HEADING.findall(text or "")
    if days:
        return max(days)
    fm, _ = split_frontmatter(text or "")
    return _fm_get(fm, "created") or fallback


def index_rows(root) -> list[tuple[str, str, str, str]]:
    root = Path(root)
    base = root / ENTITIES_DIR
    rows: list[tuple[str, str, str, str]] = []
    for kind in KINDS:
        d = base / kind
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            if f.name == "index.md":
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, _ = split_frontmatter(text)
            # `kg/templates/` is not the only place a shape can hide: a doc whose frontmatter says
            # `template: true` is a SHAPE wherever it sits, and the kg-links rule already forbids
            # citing one. Listing it here would put it back in front of the model on every turn.
            if (_fm_get(fm, "template") or "").lower() == "true":
                continue
            title = _fm_get(fm, "title") or f.stem
            try:
                mtime = _dt.date.fromtimestamp(f.stat().st_mtime).isoformat()
            except OSError:
                mtime = ""
            rows.append((kind, title, f"{ENTITIES_DIR}/{kind}/{f.name}", _last_updated(text, mtime)))
    return rows


def render_index(root, slug: str = "") -> str:
    rows = index_rows(root)
    head = f"# Entities in {slug}" if slug else "# Entities"
    if not rows:
        return (head + "\n\nNo entity pages exist here yet. The first name this turn learns something"
                " about gets one — `entity_upsert` creates it in a single call.\n")
    shown, omitted = rows[:INDEX_MAX_ROWS], max(0, len(rows) - INDEX_MAX_ROWS)
    body = [head, "", f"{len(rows)} pages. Regenerated on every `entity_upsert` — never edit by hand.",
            "", "| kind | name | path | last updated |", "|---|---|---|---|"]
    body += [f"| {k} | {t} | `{p}` | {u} |" for k, t, p, u in shown]
    if omitted:
        body.append(f"\n_{omitted} more not listed — read `{ENTITIES_DIR}/` directly for the rest._")
    return "\n".join(body) + "\n"


def write_index(root, slug: str = "") -> str:
    root = Path(root)
    p = root / INDEX_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_index(root, slug), encoding="utf-8")
    return INDEX_PATH


# ── the commit ───────────────────────────────────────────────────────────────────────────────────

def commit_entity(root, paths, *, subject_path: str, created: bool,
                  author=None):
    """ONE commit, BY PATHSPEC, carrying the F31 subject shape ``<workspace>: <path> — added|updated``.

    By pathspec on purpose, and for a standing reason rather than a local nicety: ``git commit``
    commits THE INDEX, so a bare ``add`` + ``commit`` here would sweep in whatever a concurrently
    running worker turn had staged in the same repo and file it under this message. The index is a
    write surface with no owner; naming the paths is how this writer stays in its own lane. The
    subject names the ENTITY page even when `kg/INDEX.md` rides along, because that is the change a
    reader of ``git log --oneline`` came for."""
    root = Path(root)
    if not (root / ".git").is_dir():
        return None
    env = {**os.environ, "GIT_COMMITTER_NAME": "Vexa", "GIT_COMMITTER_EMAIL": "platform@vexa.ai"}
    if author:
        env["GIT_AUTHOR_NAME"], env["GIT_AUTHOR_EMAIL"] = author

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env)

    paths = [str(p) for p in paths if p]
    if not paths:
        return None
    git("add", "--", *paths)
    if not git("diff", "--cached", "--name-only", "--", *paths).stdout.strip():
        return None
    subject = f"{root.name}: {subject_path} — {'added' if created else 'updated'}"[:72]
    if git("commit", "-m", subject, "--", *paths).returncode != 0:
        return None
    return git("rev-parse", "HEAD").stdout.strip() or None
