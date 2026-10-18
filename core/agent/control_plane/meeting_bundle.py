"""meeting_bundle.py — the agent domain's half of a meeting-bundle.v1: a meeting's WORKSPACE tree and
its NOTES PAGE, out of one deployment and into another.

meeting-api writes the bundle and lands the meeting (row, transcript, annotations, recordings). What
it cannot reach is what this domain owns: the shared workspace a meeting is bound to
(`data.workspace_id`), and the meeting's page on the owner's desk (`kg/entities/meeting/…`,
`meeting_note`). The two domains do not call each other (P9), so the person's client carries the
halves between them:

  EXPORT   `snapshot` → a PARTS archive (`workspace/<path>` entries + `notes.md`), which the client
           hands to meeting-api's `POST /meetings/{id}/export` to be placed in the bundle.
  IMPORT   after meeting-api has created the meeting, `restore` takes the SAME bundle file and puts
           its workspace part into a new private workspace of the importer and its notes page on the
           importer's desk, bound to the new meeting.

Both are the OWNER's acts and nobody else's: the routes (`routers/meeting_bundle.py`) refuse any
caller the meetings domain does not report as the meeting's owner. The archive format, its path
grammar, its caps and its refusals are the contract's (`shared/meeting_bundle_codec.py`, vendored
verbatim from `deploy/contracts/meeting-bundle.v1/bundle_codec.py`); nothing here re-spells them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

from shared import meeting_bundle_codec as codec
from workspaces.shared import workspace_paths as wpaths

from control_plane import meeting_mint, meeting_note

#: Top-level folders of a shared workspace that are NOT meeting material: the roster and policy name
#: this deployment's subjects and grants, which mean nothing (and must not travel) anywhere else.
EXCLUDED_TOP = frozenset({"policy"})
#: The most files one export carries; one fewer than the contract's listed-file cap leaves room for
#: the bundle's own parts.
MAX_WORKSPACE_FILES = 1900
#: Where a restore records which bundle it already restored, per subject (the same `.imports` home
#: `workspace_import` keeps its job state in).
IMPORTS_DIR = ".imports/meeting-bundles"


class RestoreRefused(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


def snapshot(root, subject: str, row: dict, *, workspace_dir: Optional[Path]) -> "tuple[list, Optional[bytes], list]":
    """``(workspace [(path, bytes)], notes_page | None, skipped [rel])`` for the meeting ``row``.

    ``workspace_dir`` is the bound shared workspace's tree when the caller may read it, else None.
    A file whose NAME the contract's path grammar does not admit, or that is larger than its cap,
    is not silently dropped: it is returned in ``skipped`` and the route reports it."""
    notes = None
    rel = meeting_note.resolve(root, subject, row)
    if rel:
        notes = wpaths.read_bytes_inside(Path(root) / str(subject), rel,
                                         max_bytes=codec.MAX_NOTES_PAGE_BYTES)
    files, skipped, total = [], [], 0
    if workspace_dir is not None:
        def skip_dir(rel_dir: str) -> bool:
            # The roster and policy, and every dot-folder (git, the platform's own state): plumbing,
            # not meeting material — never carried, and not counted as skipped.
            return (rel_dir.split("/", 1)[0] in EXCLUDED_TOP
                    or rel_dir.rsplit("/", 1)[-1].startswith("."))

        for rel_path in sorted(wpaths.walk_files_inside(workspace_dir, skip_dir=skip_dir)):
            if rel_path.rsplit("/", 1)[-1].startswith("."):
                continue
            path = f"workspace/{rel_path}"
            if not codec.is_bundle_path(path):
                skipped.append(rel_path)
                continue
            data = wpaths.read_bytes_inside(workspace_dir, rel_path, max_bytes=codec.MAX_WORKSPACE_FILE_BYTES)
            if data is None:
                skipped.append(rel_path)
                continue
            total += len(data)
            files.append((path, data))
            if len(files) > MAX_WORKSPACE_FILES or total > codec.MAX_PARTS_BYTES:
                raise RestoreRefused(413, "too_large",
                                     f"the meeting's workspace is larger than one bundle carries "
                                     f"({MAX_WORKSPACE_FILES} files, {codec.MAX_PARTS_BYTES} bytes)")
    return files, notes, skipped


def _marker(root, subject: str, bundle_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", bundle_id):
        raise RestoreRefused(422, "invalid_part", "bundle_id is not a uuid")
    return Path(root) / IMPORTS_DIR / str(subject) / f"{bundle_id}.json"


def _rebind_page(text: str, old_id: int, new_id: int) -> str:
    """The page names the meeting it belongs to twice — its `meeting:` frontmatter and its live
    transcript slot. On the importing deployment that is the NEW meeting; left alone, the page would
    render the transcript of whatever meeting holds the old number here."""
    text = re.sub(rf"(?m)^meeting:[ \t]*{old_id}[ \t]*$", f"meeting: {new_id}", text, count=1)
    return re.sub(rf"<!--\s*vexa:transcript meeting={old_id}\s*-->",
                  f"<!-- vexa:transcript meeting={new_id} -->", text)


def restore(root, subject: str, row: dict, parsed, *, record_note: Callable,
            create_workspace: Callable, slot_dir: Callable, commit: Callable) -> dict:
    """Land ``parsed``'s workspace and notes page for ``subject``, bound to the meeting ``row`` that
    meeting-api created from THE SAME bundle. Refused (``RestoreRefused``) for a row this bundle did
    not create, and for a bundle this person already restored."""
    meta = ((row.get("data") or {}).get("metadata") or {}) if isinstance(row.get("data"), dict) else {}
    if meta.get("imported_bundle_id") != parsed.bundle_id:
        raise RestoreRefused(409, "manifest_mismatch",
                             "this meeting was not imported from this bundle — import the bundle first")
    marker = _marker(root, subject, parsed.bundle_id)
    if marker.is_file():
        prior = json.loads(marker.read_text(encoding="utf-8"))
        raise RestoreRefused(409, "duplicate_import",
                             f"this bundle's workspace was already restored (workspace {prior.get('workspace') or 'none'})")
    out = {"meeting_id": row.get("id"), "workspace": None, "notes_page": None}

    if parsed.notes_page is not None:
        desk = Path(root) / str(subject)
        rel = meeting_note.recorded_path(row) or meeting_mint.compose(row)
        written = False
        if not wpaths.is_file_inside(desk, rel):
            page = _rebind_page(parsed.notes_page, int(parsed.manifest["source"]["meeting_id"]), int(row["id"]))
            wpaths.write_text_inside(desk, rel, page)
            commit(desk, [rel])
            record_note(subject, str(row["id"]), rel)
            written = True
        out["notes_page"] = {"path": rel, "written": written}

    if parsed.workspace:
        title = str((row.get("data") or {}).get("title") or "Meeting")[:80]
        created = create_workspace(root, subject, name=f"{title} (imported)")
        target = slot_dir(root, subject, created.slug)
        rels = []
        for path, data in parsed.workspace:
            rel_path = path[len("workspace/"):]
            wpaths.write_bytes_inside(target, rel_path, data)
            rels.append(rel_path)
        commit(target, rels)
        out["workspace"] = {"slug": created.slug, "files": len(rels)}

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"meeting_id": row.get("id"),
                                  "workspace": (out["workspace"] or {}).get("slug")}), encoding="utf-8")
    return out
