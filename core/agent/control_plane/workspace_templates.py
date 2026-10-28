"""workspace_templates.py — WHAT A NEW WORKSPACE CAN START AS (Vexa-ai/vexa#1783, founder 2026-10-14).

*"We should have workspace templates for different occasions, like event organization in our case."*

A template is a directory of pages plus a ``TEMPLATE.md`` manifest (title, one-line description,
when to use it, what it creates, the questions to ask the host, suggested routines). Three places
hold them, read in this order, a later one winning by name:

  1. the IMAGE's seeds (``behavior/workspace-templates/``) — read only when ``_global`` has no
     ``workspace-templates/`` directory at all (an unseeded or read-only company layer);
  2. the COMPANY's: ``_global/workspace-templates/<name>/``. ``global_seed`` copies the image's seeds
     there additively at boot and never overwrites one; from then on they are the admin account's to
     edit from chat, with the history and rollback every ``_global`` page has;
  3. the PERSON's own: ``templates/workspaces/<name>/`` in their Personal workspace, written by
     "save this workspace as a template".

Creating from a template is the existing create (``workspace_attach.create_workspace``) followed by
``apply``: the pages are copied into the fresh workspace with ``{{key}}`` filled from the host's
answers and committed. Suggested routines are returned as suggestions; nothing is switched on.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from control_plane.commit_trailers import with_trailers
from shared.gitexec import run_git
from workspaces.shared import entities as entities_mod
from workspaces.shared import workspace_paths as wpaths

#: Where the company's templates live, relative to `_global`.
COMPANY_DIR = "workspace-templates"
#: Where a person's own templates live, relative to their Personal workspace.
PERSONAL_DIR = "templates/workspaces"
#: The manifest file. Never copied into a new workspace.
MANIFEST = "TEMPLATE.md"

#: A template's name: its directory. Short kebab case, so it is safe as a path segment.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
#: Bounds — a template is a page skeleton, not an archive.
MAX_FILES = 200
MAX_FILE_BYTES = 256 * 1024
MAX_ANSWER = 500
#: What an unanswered question with no default renders as.
UNANSWERED = "_(to decide)_"
#: Files whose text gets `{{key}}` filled; anything else is copied byte for byte.
TEXT_SUFFIXES = (".md", ".txt", ".csv", ".json", ".yaml", ".yml")

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")
_QUESTION = re.compile(r"^-\s+`([a-z0-9_]+)`\s+[—-]+\s+(.+?)(?:\s+\(default:\s*(.+?)\))?\s*$")
_BULLET = re.compile(r"^-\s+(.+?)\s*$")


class TemplateError(ValueError):
    """A sentence the caller (an agent) can act on."""


@dataclass
class Question:
    key: str
    ask: str
    default: Optional[str] = None

    def as_dict(self) -> dict:
        return {"key": self.key, "ask": self.ask, "default": self.default}


@dataclass
class Template:
    name: str
    source: str              # "built-in" · "company" · "yours"
    path: Path               # the template's directory
    title: str = ""
    description: str = ""
    when: str = ""
    creates: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    routines: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"name": self.name, "title": self.title or self.name, "description": self.description,
                "when": self.when, "source": self.source, "creates": list(self.creates),
                "questions": [q.as_dict() for q in self.questions],
                "suggested_routines": list(self.routines)}


def image_dir() -> Optional[Path]:
    """The image's seeds (`global_seed` owns the path: the agent-api Dockerfile COPYs
    `behavior/workspace-templates` there)."""
    from control_plane import global_seed
    return global_seed.image_workspace_templates_dir()


def _sections(body: str) -> dict:
    out: dict = {}
    current = None
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            out[current] = []
        elif current is not None and line.strip():
            out[current].append(line.rstrip())
    return out


def parse_manifest(name: str, source: str, path: Path, text: str) -> Template:
    fm, body = entities_mod.split_frontmatter(text or "")

    def get(key: str) -> str:
        v = entities_mod._fm_get(fm, key)
        return (v or "").strip().strip('"').strip("'")

    t = Template(name=name, source=source, path=path, title=get("title"),
                 description=get("description"), when=get("when"))
    sec = _sections(body)
    for line in sec.get("creates", []):
        m = _BULLET.match(line)
        if m:
            t.creates.append(m.group(1))
    for line in sec.get("questions", []):
        m = _QUESTION.match(line)
        if m:
            t.questions.append(Question(m.group(1), m.group(2).strip(), (m.group(3) or None)))
    for line in sec.get("suggested routines", []):
        m = _BULLET.match(line)
        if m:
            t.routines.append(m.group(1))
    return t


def _read_dir(root: Path, rel_dir: str, source: str) -> dict:
    """Every valid template directly under ``root/<rel_dir>``, by name."""
    out: dict = {}
    if root is None or not Path(root).is_dir():
        return out
    for name in wpaths.list_dirs_inside(root, rel_dir):
        if not NAME_RE.match(name):
            continue
        rel = f"{rel_dir}/{name}" if rel_dir else name
        text = wpaths.read_text_inside(root, f"{rel}/{MANIFEST}", max_bytes=MAX_FILE_BYTES)
        if text is None:
            continue
        out[name] = parse_manifest(name, source, Path(root) / rel, text)
    return out


def list_templates(global_root: "Path | None", personal_root: "Path | None") -> list:
    """Every template this person can create from, the person's own winning by name."""
    found: dict = {}
    has_company = global_root is not None and wpaths.is_dir_inside(global_root, COMPANY_DIR)
    if not has_company:
        img = image_dir()
        if img is not None:
            found.update(_read_dir(img, "", "built-in"))
    else:
        found.update(_read_dir(Path(global_root), COMPANY_DIR, "company"))
    if personal_root is not None:
        found.update(_read_dir(Path(personal_root), PERSONAL_DIR, "yours"))
    return sorted(found.values(), key=lambda t: (t.source != "yours", (t.title or t.name).lower()))


def find(global_root, personal_root, wanted: str) -> Template:
    """The template a person named — by its name or its title, case-insensitive."""
    w = (wanted or "").strip().lower()
    all_ = list_templates(global_root, personal_root)
    for t in all_:
        if t.name == w or (t.title or "").lower() == w:
            return t
    names = ", ".join(t.name for t in all_) or "none"
    raise TemplateError(f"no workspace template called {wanted!r} — the templates are: {names}")


def _clean_answer(v) -> str:
    s = str(v if v is not None else "").replace("{{", "").replace("}}", "").strip()
    return s[:MAX_ANSWER]


def fill(text: str, values: dict) -> str:
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), UNANSWERED), text)


def values_for(t: Template, answers: "dict | None", workspace_name: str, today: Optional[str] = None) -> dict:
    answers = answers or {}
    vals = {"workspace_name": workspace_name, "today": today or _dt.date.today().isoformat()}
    for q in t.questions:
        given = _clean_answer(answers.get(q.key))
        vals[q.key] = given or (q.default or UNANSWERED)
    return vals


def _pages(t: Template):
    """(workspace-relative path) of every page the template creates, manifest and dotfiles skipped."""
    root = t.path
    n = 0
    for rel in wpaths.walk_files_inside(root, ""):
        if rel == MANIFEST or any(p.startswith(".") for p in rel.split("/")):
            continue
        n += 1
        if n > MAX_FILES:
            break
        yield rel


def apply(t: Template, ws_dir: Path, answers: "dict | None", *, workspace_name: str,
          today: Optional[str] = None, trailers: Iterable[str] = ()) -> dict:
    """Write the template's pages into ``ws_dir`` (a freshly created workspace) and commit them,
    closing the message with ``trailers`` (who made it when an agent did, ``commit_trailers``)."""
    vals = values_for(t, answers, workspace_name, today)
    written: list = []
    for rel in _pages(t):
        data = wpaths.read_bytes_inside(t.path, rel, max_bytes=MAX_FILE_BYTES)
        if data is None:
            continue
        if rel.lower().endswith(TEXT_SUFFIXES):
            data = fill(data.decode("utf-8", errors="replace"), vals).encode("utf-8")
        try:
            wpaths.write_bytes_inside(ws_dir, rel, data)
        except (OSError, ValueError):
            continue
        written.append(rel)
    if (Path(ws_dir) / ".git").exists() and written:
        run_git(ws_dir, "add", "-A", check=True)
        run_git(ws_dir, "commit", "-q", "-m", with_trailers(f"template: {t.name}", trailers), check=True)
    keys = {q.key for q in t.questions}
    return {
        "template": t.name, "pages": written,
        "unanswered": [q.key for q in t.questions if not _clean_answer((answers or {}).get(q.key))
                       and not q.default],
        "ignored_answers": sorted(k for k in (answers or {}) if k not in keys),
        "suggested_routines": list(t.routines),
    }


# ── save as template ────────────────────────────────────────────────────────────────────────

#: Directories never carried into a template: history, the person's own entity records (data, not
#: shape), and templates themselves.
_SKIP_DIRS = (".git", "kg/entities", "templates", "flows", "skills", "views")
#: Seed files every workspace already has — a template need not carry them.
_SKIP_FILES = ("CLAUDE.md",)


def _skip_dir(rel: str) -> bool:
    rel = rel.strip("/")
    return any(rel == d or rel.startswith(d + "/") for d in _SKIP_DIRS) or rel.split("/")[-1].startswith(".")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:48].strip("-")
    return s


def save(ws_dir: Path, personal_root: Path, name: str, *, title: str = "", description: str = "",
         when: str = "", replace: bool = False, trailers: Iterable[str] = ()) -> dict:
    """Copy a workspace's pages into the person's own template ``name`` and commit it there (closed
    with ``trailers``, as ``apply``)."""
    slug = slugify(name)
    if not slug or not NAME_RE.match(slug):
        raise TemplateError("give the template a short name, e.g. 'board-meeting'")
    rel_dir = f"{PERSONAL_DIR}/{slug}"
    if wpaths.is_dir_inside(personal_root, rel_dir) and not replace:
        raise TemplateError(f"you already have a template called {slug!r} — say replace to overwrite it, "
                            "or pick another name")
    pages = [rel for rel in wpaths.walk_files_inside(ws_dir, "", skip_dir=_skip_dir)
             if rel.lower().endswith(".md") and rel not in _SKIP_FILES and rel != MANIFEST
             and not rel.split("/")[-1].startswith(".")][:MAX_FILES]
    if not pages:
        raise TemplateError("that workspace has no pages to make a template from")
    if wpaths.is_dir_inside(personal_root, rel_dir):
        for old in list(wpaths.walk_files_inside(personal_root, rel_dir)):
            wpaths.unlink_inside(personal_root, old)
    for rel in pages:
        data = wpaths.read_bytes_inside(ws_dir, rel, max_bytes=MAX_FILE_BYTES)
        if data is not None:
            wpaths.write_bytes_inside(personal_root, f"{rel_dir}/{rel}", data)
    title = (title or name).strip()[:80]
    manifest = ("---\n"
                f"title: {title}\n"
                f"description: {(description or f'Starts like {title}.').strip()[:200]}\n"
                f"when: {(when or '').strip()[:200]}\n"
                "---\n## Creates\n" + "".join(f"- {p}\n" for p in pages) +
                "\n## Questions\n\n## Suggested routines\n")
    wpaths.write_text_inside(personal_root, f"{rel_dir}/{MANIFEST}", manifest)
    if (Path(personal_root) / ".git").exists():
        run_git(personal_root, "add", "-A", "--", rel_dir, check=True)
        run_git(personal_root, "commit", "-q", "-m", with_trailers(f"template saved: {slug}", trailers), "--", rel_dir,
                check=True)
    return {"name": slug, "title": title, "pages": pages, "path": rel_dir, "source": "yours"}
