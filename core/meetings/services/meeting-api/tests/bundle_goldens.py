"""The meeting-bundle.v1 golden bundles — how each is made, in one place.

`deploy/contracts/meeting-bundle.v1/golden/bundles/*.zip` are written by the SHIPPED exporter from the
fixed inputs below, and `golden/refused/*.zip` are hand-made archives each breaking one rule. The
conformance test (`test_meeting_bundle_contract.py`) rebuilds every one and requires the committed
bytes to be identical, so a golden can only change with the code that writes it; the Node validator
(`validate.mjs`) re-derives the verdicts in a second language from the same files.

Regenerate after an intended change: `MEETING_BUNDLE_REGEN=1 uv run pytest -q tests/test_meeting_bundle_contract.py`.
"""
from __future__ import annotations

import io
import json
import stat
import zipfile
from hashlib import sha256
from pathlib import Path

from meeting_api.bundle import MediaBlob, write_bundle, write_parts

CONTRACT_DIR = next(p / "deploy" / "contracts" / "meeting-bundle.v1"
                    for p in Path(__file__).resolve().parents
                    if (p / "deploy" / "contracts" / "meeting-bundle.v1").is_dir())
GOLDEN = CONTRACT_DIR / "golden"

# Fixed identities so the bytes are reproducible. The deployment id is what `deployment_id()` derives
# from GOLDEN_SECRET (asserted by the test, so the two cannot drift).
GOLDEN_SECRET = "meeting-bundle-golden-deployment"
BUNDLE_ID = "6f1c2b9e-3d4a-4c5b-8e7f-0a1b2c3d4e5f"
EXPORTED_AT = "2026-10-10T12:00:00Z"
SOURCE_MEETING_ID = 4242

# A WebM/Matroska container starts with the EBML magic; a WAV is RIFF....WAVE. Tiny, but real headers.
WEBM_AUDIO = b"\x1a\x45\xdf\xa3" + bytes(range(60))
WAV_AUDIO = (b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
             + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little")
             + (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
             + b"data" + (0).to_bytes(4, "little"))

SEGMENTS = [
    {"start": 0.0, "end": 4.5, "speaker": "Ada", "text": "Welcome, everyone. Two items today.", "language": "en"},
    {"start": 4.5, "end": 9.25, "speaker": "Grace", "text": "First, the release checklist.", "language": "en"},
    {"start": 9.25, "end": 15.0, "speaker": "Ada", "text": "Then the migration plan <b>draft</b>.", "language": "en"},
]


def meeting(media: list) -> dict:
    return {
        "platform": "jitsi",
        "native_meeting_id": "bundle-golden-room",
        "title": "Release sync",
        "status": "completed",
        "start_time": "2026-10-01T14:00:00Z",
        "end_time": "2026-10-01T14:00:15Z",
        "participants": ["Ada", "Grace"],
        "media": [{"path": m.path, "type": m.type, "format": m.format,
                   "duration_seconds": m.duration_seconds} for m in media],
    }


ANNOTATIONS = {"metadata": {"project": "release-0.13", "tags": ["sync", "weekly"]}, "notes": None}

# The agent half: a workspace tree and the meeting's page.
WORKSPACE = [
    ("workspace/notes/agenda.md", b"# Agenda\n\n1. Release checklist\n2. Migration plan\n"),
    ("workspace/kg/entities/person/ada.md", b"# Ada\n\nRuns the release.\n"),
]
NOTES_PAGE = (b"---\ntype: meeting\nmeeting: 4242\ntitle: Release sync\n---\n\n# Release sync\n\n"
              b"<!-- vexa:transcript meeting=4242 -->\n\n<!-- meeting:decisions:start -->\n- ship 0.13\n"
              b"<!-- meeting:decisions:end -->\n")


def golden_bundle(name: str) -> bytes:
    media = []
    if name == "with-audio":
        media = [MediaBlob(path="media/recording-1-audio.webm", type="audio", format="webm",
                           duration_seconds=15.0, data=WEBM_AUDIO)]
    if name == "with-workspace-and-notes":
        return write_bundle(
            meeting=meeting([]), transcript={"segments": SEGMENTS},
            annotations={**ANNOTATIONS, "notes": "Follow up on the migration plan."},
            media=[], bundle_id=BUNDLE_ID[:-1] + "7", exported_at=EXPORTED_AT,
            deployment_id=_golden_deployment_id(), source_meeting_id=SOURCE_MEETING_ID,
            workspace=WORKSPACE, notes_page=NOTES_PAGE)
    return write_bundle(
        meeting=meeting(media), transcript={"segments": SEGMENTS}, annotations=ANNOTATIONS,
        media=media, bundle_id=BUNDLE_ID if name == "transcript-only" else BUNDLE_ID[:-1] + "6",
        exported_at=EXPORTED_AT, deployment_id=_golden_deployment_id(), source_meeting_id=SOURCE_MEETING_ID,
    )


def golden_parts() -> bytes:
    """The parts archive agent-api hands an export (golden/parts/workspace-and-notes.zip)."""
    return write_parts(WORKSPACE, NOTES_PAGE)


def _golden_deployment_id() -> str:
    from meeting_api.bundle import deployment_id

    return deployment_id(GOLDEN_SECRET)


GOOD = ("transcript-only", "with-audio", "with-workspace-and-notes")


# ── refused ─────────────────────────────────────────────────────────────────────────────────────
def _zip(entries: list) -> bytes:
    """entries: (name, bytes, {mode?, compress?})."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data, opts in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = opts.get("compress", zipfile.ZIP_DEFLATED)
            info.create_system = 3
            info.external_attr = (opts.get("mode", stat.S_IFREG | 0o644)) << 16
            zf.writestr(info, data)
    return out.getvalue()


def _parts(meeting_doc=None, transcript=None, annotations=None, extra=()):
    parts = [
        ("meeting.json", "meeting", json.dumps(meeting_doc or meeting([])).encode()),
        ("transcript.json", "transcript", json.dumps(transcript or {"segments": SEGMENTS}).encode()),
        ("annotations.json", "annotations", json.dumps(annotations or ANNOTATIONS).encode()),
    ]
    return parts + list(extra)


def _manifest(parts, **over) -> bytes:
    doc = {
        "contract": "meeting-bundle.v1", "format_version": "1.0", "bundle_id": BUNDLE_ID,
        "exported_at": EXPORTED_AT,
        "source": {"deployment_id": _golden_deployment_id(), "meeting_id": SOURCE_MEETING_ID},
        "files": [{"path": p, "role": r, "bytes": len(b), "sha256": sha256(b).hexdigest()} for p, r, b in parts],
    }
    doc.update(over)
    return json.dumps(doc).encode()


def _archive(parts, manifest=None, extra_entries=()) -> bytes:
    entries = [("manifest.json", manifest if manifest is not None else _manifest(parts), {})]
    entries += [(p, b, {}) for p, _r, b in parts]
    entries += list(extra_entries)
    return _zip(entries)


def refused_bundle(name: str) -> bytes:
    parts = _parts()
    if name == "zip-slip":
        return _archive(parts, extra_entries=[("../../etc/cron.d/evil", b"* * * * * root id\n", {})])
    if name == "absolute-path":
        return _archive(parts, extra_entries=[("/tmp/evil.sh", b"#!/bin/sh\n", {})])
    if name == "backslash-path":
        return _archive(parts, extra_entries=[("workspace\\..\\evil.txt", b"x", {})])
    if name == "symlink":
        return _archive(parts, extra_entries=[("workspace/link", b"/etc/passwd", {"mode": stat.S_IFLNK | 0o777})])
    if name == "hash-mismatch":
        manifest = _manifest(parts)
        tampered = [(p, r, (b.replace(b"release checklist", b"release CHECKLIST") if p == "transcript.json" else b))
                    for p, r, b in parts]
        return _archive(tampered, manifest=manifest)
    if name == "unknown-version":
        return _archive(parts, manifest=_manifest(parts, format_version="2.0"))
    if name == "unlisted-entry":
        return _archive(parts, extra_entries=[("workspace/notes.md", b"# not in the manifest\n", {})])
    if name == "decompression-bomb":
        bomb = ("workspace/zeros.txt", "workspace", b"\x00" * (4 * 1024 * 1024))
        return _archive(parts + [bomb])
    if name == "not-a-zip":
        return b"<html><script>alert(1)</script></html>"
    if name == "missing-manifest":
        return _zip([(p, b, {}) for p, _r, b in parts])
    if name == "fake-media":
        media = [MediaBlob(path="media/recording-1-audio.webm", type="audio", format="webm",
                           duration_seconds=1.0, data=b"MZ\x90\x00 an executable, not a webm")]
        m = ("media/recording-1-audio.webm", "media", media[0].data)
        return _archive(_parts(meeting_doc=meeting(media), extra=[m]))
    if name == "notes-not-text":
        notes = ("notes.md", "notes", b"\x89PNG\r\n\x1a\n\x00\x00 binary, not a page")
        return _archive(parts + [notes])
    if name == "parts-foreign-entry":
        return _zip([("notes.md", NOTES_PAGE, {}), ("meeting.json", b"{}", {})])
    if name == "parts-zip-slip":
        return _zip([("workspace/../../etc/passwd", b"x", {})])
    if name == "deployment-bound-field":
        leaky = {**meeting([]), "user_id": 7, "storage_path": "recordings/7/1/x/audio/master.webm"}
        return _archive(_parts(meeting_doc=leaky))
    raise KeyError(name)


#: name → the refusal code every importer gives it, and why (golden/refused/refused.json).
REFUSED = {
    "zip-slip": ("unsafe_path", "an entry whose name climbs out of the archive with '..'"),
    "absolute-path": ("unsafe_path", "an entry with an absolute path"),
    "backslash-path": ("unsafe_path", "an entry name using a backslash separator"),
    "symlink": ("unsafe_entry", "an entry that is a symbolic link"),
    "hash-mismatch": ("hash_mismatch", "a part changed after the manifest hashed it"),
    "unknown-version": ("unsupported_version", "a format_version whose major this contract does not define"),
    "unlisted-entry": ("manifest_mismatch", "an entry the manifest does not list"),
    "decompression-bomb": ("too_large", "a 4 MiB part that deflates past the 200:1 inflation cap"),
    "not-a-zip": ("not_a_bundle", "bytes that are not a zip archive"),
    "missing-manifest": ("not_a_bundle", "a zip without manifest.json"),
    "fake-media": ("unsafe_media", "a media part whose bytes are not the container its name claims"),
    "deployment-bound-field": ("invalid_part", "meeting.json carrying a user id and a storage path"),
    "notes-not-text": ("invalid_part", "a notes.md part that is binary, not UTF-8 text"),
}

#: Parts archives an export must refuse (golden/parts/refused.json).
REFUSED_PARTS = {
    "parts-foreign-entry": ("manifest_mismatch", "a parts archive carrying something other than workspace/ and notes.md"),
    "parts-zip-slip": ("unsafe_path", "a workspace entry that climbs out with '..'"),
}
