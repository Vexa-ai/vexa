"""attachments.py — the files a person attaches to a chat message, as ONE mark on the prompt.

WHAT WAS WRONG (founder, 2026-10-13). A pasted screenshot rendered in his own bubble as the text
``Attached files: 9caa6e9e467d2341-Screenshot_….png``, and the agent's turn opened with a Glob: the
line it was handed named the file relative to a workspace and nothing said where that workspace was.

THE SHAPE NOW. The terminal writes one machine mark after the person's words:

    <!--vexa:attachments [{"path": "assets/<sha16>-<name>", "name": "<original name>"}]-->

agent-api normalises it (``normalise``): every item must be a file the speaker's own upload route
stored in their desk, and the server — never the client — fills in whose desk (``ws``), the size and
the media type. The worker expands it (``expand``) into the same mark carrying each file's ABSOLUTE
path inside the turn's mounts plus a short block the model reads, so the agent opens the file
directly. Each harness that can take a picture natively reads ``images`` off the same prompt and
sends the image as an image (``llm/claude_code.py``, ``llm/codex.py``, ``llm/openai_agent.py``).

The history reader serves the items as ``attachments`` on the user turn (``read``) and the person's
words without the block (``strip``), so the bubble shows thumbnails and never the mark. ``legacy``
reads the ``Attached files:`` lines turns carried before this mark existed, so chats already in
people's transcripts render the same way.

The mark is an HTML comment on purpose: a deployment whose reader is one release behind shows the
person's words with an invisible comment, never a broken bubble.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

from shared.asset_source import media_type_for

MARK_OPEN = "<!--vexa:attachments "
MARK_CLOSE = "-->"
#: Closes the block the worker writes under the mark, so ``strip`` removes exactly what was added.
BLOCK_END = "<!--/vexa:attachments-->"
#: The most files one message carries.
MAX_ITEMS = 10
#: Where the upload route stores an attachment: pictures in ``assets/``, everything else ``uploads/``.
FOLDERS = ("assets", "uploads")
#: The name the upload route stores: ``<folder>/<first 16 hex of the sha256>-<sanitised name>``.
STORED = re.compile(r"^(assets|uploads)/([0-9a-f]{16})-([A-Za-z0-9._-]{1,160})$")
#: Pictures a model takes as an image block (Claude and OpenAI vision agree on these four).
NATIVE_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
#: The largest picture sent as an image block. Claude refuses an image over 5 MB; the margin is the
#: base64 overhead some transports count. A larger picture is still attached by path.
MAX_NATIVE_IMAGE_BYTES = 3_750_000

_MARK_RE = re.compile(re.escape(MARK_OPEN) + r"(\[.*?\])" + re.escape(MARK_CLOSE), re.S)
_LEGACY_HEAD = re.compile(r"(?:^|\n)Attached files:[ \t]*(?:\n|$)")
_LEGACY_IMG = re.compile(r"^- !\[([^\]\n]*)\]\(((?:assets|uploads)/[^)\s]+)\)")
_LEGACY_FILE = re.compile(r"^- @file:((?:assets|uploads)/\S+)")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def original_name(stored: str) -> str:
    """The name a person would recognise: the stored file's last segment without the hash prefix."""
    base = str(stored or "").rsplit("/", 1)[-1]
    m = re.match(r"^[0-9a-f]{16}-(.+)$", base)
    return m.group(1) if m else base


def kind_of(media_type: str) -> str:
    return "image" if str(media_type or "").startswith("image/") else "file"


def _clean_name(name, path: str) -> str:
    n = _CTRL.sub("", str(name or "")).strip()[:200]
    return n or original_name(path)


def compose(items: list[dict]) -> str:
    """The mark for ``items`` (``[]`` → ``""``)."""
    if not items:
        return ""
    return MARK_OPEN + json.dumps(list(items)[:MAX_ITEMS], ensure_ascii=False,
                                  separators=(",", ":")) + MARK_CLOSE


def _raw(text: str) -> Optional[list]:
    """The items in the LAST mark of ``text``, unvalidated, or None when there is no mark."""
    found = None
    for m in _MARK_RE.finditer(text or ""):
        found = m
    if found is None:
        return None
    try:
        data = json.loads(found.group(1))
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def read(text: str) -> Optional[list[dict]]:
    """The attachments a stored prompt carries, for the bubble: ``name``, ``path``, ``ws``, ``type``,
    ``size``, ``kind``. Never an absolute path — that is the worker's, and means nothing to a
    browser. None when the prompt carries none (the legacy lines included)."""
    items = _raw(text)
    if items is None:
        old = legacy(text)
        return old or None
    out = []
    for it in items:
        path = str(it.get("path") or "")
        if not STORED.fullmatch(path):
            continue
        mt = str(it.get("type") or media_type_for(path))
        row = {"name": _clean_name(it.get("name"), path), "path": path, "type": mt, "kind": kind_of(mt)}
        if it.get("ws"):
            row["ws"] = str(it["ws"])
        if isinstance(it.get("size"), int):
            row["size"] = it["size"]
        out.append(row)
    return out or None


def legacy(text: str) -> list[dict]:
    """The items of an ``Attached files:`` block (the terminal's wording before this mark)."""
    m = None
    for m in _LEGACY_HEAD.finditer(text or ""):
        pass
    if m is None:
        return []
    out = []
    for line in (text or "")[m.end():].splitlines():
        line = line.strip()
        img = _LEGACY_IMG.match(line)
        f = None if img else _LEGACY_FILE.match(line)
        if not img and not f:
            break
        path = img.group(2) if img else f.group(1)
        if not STORED.fullmatch(path):
            continue
        mt = media_type_for(path)
        out.append({"name": original_name(path), "path": path, "type": mt, "kind": kind_of(mt)})
    return out


def strip(text: str) -> str:
    """``text`` without the attachments: the mark, the block the worker wrote under it, and a legacy
    ``Attached files:`` block. What is left is what the person typed."""
    t = text or ""
    while True:
        i = t.find(MARK_OPEN)
        if i < 0:
            break
        j = t.find(BLOCK_END, i)
        if j >= 0:
            t = t[:i] + t[j + len(BLOCK_END):]
            continue
        k = t.find(MARK_CLOSE, i + len(MARK_OPEN))
        t = t[:i] + (t[k + len(MARK_CLOSE):] if k >= 0 else "")
    m = None
    for m in _LEGACY_HEAD.finditer(t):
        pass
    if m is not None and legacy(t[m.start():]):
        tail = t[m.end():].splitlines()
        rest = [ln for ln in tail if not (_LEGACY_IMG.match(ln.strip()) or _LEGACY_FILE.match(ln.strip()))]
        t = t[:m.start()] + ("\n" + "\n".join(rest) if any(r.strip() for r in rest) else "")
    return t.strip()


def normalise(text: str, *, owner: str, stat: Callable[[str], Optional[int]],
              where: Optional[Callable[[str], str]] = None) -> tuple[str, list[dict]]:
    """agent-api's half: the prompt with its mark rewritten to what the server vouches for.

    Each item must name a file the upload route stored (``STORED``) that exists in ``owner``'s own
    desk (``stat(path)`` answers its size, or None). Anything else is dropped. ``ws``, ``size`` and
    ``type`` are the server's; only ``name`` comes from the client, cleaned. Returns the new prompt
    and the kept items."""
    items = _raw(text)
    if items is None:
        return text, []
    kept: list[dict] = []
    seen: set = set()
    for it in items:
        path = str(it.get("path") or "").strip()
        if path in seen or not STORED.fullmatch(path):
            continue
        size = stat(path)
        if size is None:
            continue
        seen.add(path)
        kept.append({"ws": (where(path) if where is not None else "") or owner, "path": path, "name": _clean_name(it.get("name"), path),
                     "type": media_type_for(path), "size": int(size)})
        if len(kept) >= MAX_ITEMS:
            break
    body = strip(text)
    mark = compose(kept)
    return ((body + "\n\n" + mark) if body and mark else (body or mark)), kept


def merge(text: str, extra: list[dict]) -> str:
    """``text`` with ``extra`` items added to its mark (by path, the prompt's own first)."""
    if not extra:
        return text
    items = list(_raw(text) or [])
    seen = {str(i.get("path") or "") for i in items}
    for it in extra:
        p = str(it.get("path") or "")
        if p and p not in seen and STORED.fullmatch(p):
            items.append(dict(it))
            seen.add(p)
    body = strip(text)
    mark = compose(items)
    return (body + "\n\n" + mark) if body else mark


def _size(n) -> str:
    if not isinstance(n, int):
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def expand(text: str, resolve: Callable[[str, str], Optional[str]]) -> str:
    """The worker's half: the mark with each item's absolute path (``abs``) and, under it, the block
    the model reads. ``resolve(ws, path)`` answers where that file is inside this turn's mounts, or
    None when the turn cannot read it (a shared conversation does not mount anyone's desk)."""
    items = _raw(text)
    if not items:
        return text
    rows, lines = [], []
    for it in items:
        path = str(it.get("path") or "")
        if not STORED.fullmatch(path):
            continue
        it = dict(it)
        it.pop("abs", None)
        name = _clean_name(it.get("name"), path)
        mt = str(it.get("type") or media_type_for(path))
        where = resolve(str(it.get("ws") or ""), path)
        facts = " · ".join(x for x in (mt, _size(it.get("size"))) if x)
        if where:
            it["abs"] = where
            lines.append(f"- `{name}` ({facts}): `{where}`")
        else:
            lines.append(f"- `{name}` ({facts}): not readable in this conversation — the file is in "
                         "the speaker's own workspace, which this turn does not mount. Say so if "
                         "you need it; do not search for it.")
        rows.append(it)
    if not rows:
        return strip(text)
    block = "\n".join([
        compose(rows),
        "## Attached to this message",
        "",
        "The person attached these files. Each path is exact: open it directly, never search for "
        "it. A picture is also in this message as an image when your model accepts images.",
        "",
        *lines,
        BLOCK_END,
    ])
    body = strip(text)
    return (body + "\n\n" + block) if body else block


def images(text: str) -> list[dict]:
    """The pictures a harness may send natively: items of the mark the worker resolved (``abs``),
    of a type models accept, small enough. Each ``{"path": abs, "type": media type, "name"}``."""
    out = []
    for it in _raw(text) or []:
        where = str(it.get("abs") or "")
        mt = str(it.get("type") or media_type_for(where))
        if not where or mt not in NATIVE_IMAGE_TYPES:
            continue
        size = it.get("size")
        if isinstance(size, int) and size > MAX_NATIVE_IMAGE_BYTES:
            continue
        out.append({"path": where, "type": mt, "name": _clean_name(it.get("name"), str(it.get("path") or ""))})
    return out


def read_image(item: dict) -> Optional[bytes]:
    """The bytes of one ``images`` item, or None (gone, a link, or too large). Never follows a link."""
    from workspaces.shared import workspace_paths as wpaths  # noqa: PLC0415

    p = Path(item.get("path") or "")
    if not p.is_absolute() or not p.name:
        return None
    try:
        return wpaths.read_bytes_inside(p.parent, p.name, max_bytes=MAX_NATIVE_IMAGE_BYTES)
    except (OSError, wpaths.PathRefused):
        return None
