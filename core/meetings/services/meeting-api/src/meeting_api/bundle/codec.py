"""meeting-bundle.v1 — write and read the portable meeting file. PURE: bytes in, bytes or a parsed
bundle out; no store, no storage, no HTTP.

The contract is ``deploy/contracts/meeting-bundle.v1`` (schema, goldens, README). This module is
its Python implementation on both sides: ``write_bundle`` is the only exporter and ``read_bundle``
is the only importer's front gate. The conformance test holds both to the same goldens the Node
validator re-derives, so a bundle this module writes is a bundle any importer reading the contract
accepts, and a bundle the goldens say is refused is refused here with the same code.

What ``read_bundle`` refuses, before a single byte reaches a store (each a :class:`BundleRefused`
carrying one contract ``Refusal`` code):

* an archive that is not a zip, or has no ``manifest.json`` (``not_a_bundle``);
* a ``contract``/``format_version`` major this code does not know (``unsupported_version``);
* an entry name that is absolute, has a backslash, a drive letter, a ``.``/``..`` segment, or does
  not match the contract's path grammar (``unsafe_path``) — the zip-slip class, refused by NAME so
  nothing is ever joined onto a filesystem path;
* a symlink, a device or other special entry, an encrypted entry, a compression method other than
  stored/deflate, a duplicate name (``unsafe_entry``);
* more entries, more bytes, or a higher inflation ratio than the caps below (``too_large``) — the
  declared sizes are checked first and the actual inflation is bounded too, so a lying header does
  not get past;
* an entry the manifest does not list, a listed file the archive lacks, or a role that disagrees
  with its path (``manifest_mismatch``);
* a length or SHA-256 that differs from the manifest (``hash_mismatch``);
* a part that does not conform to the schema (``invalid_part``);
* a media part whose bytes are not the container its extension names (``unsafe_media``).
"""
from __future__ import annotations

import io
import json
import re
import stat
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Optional

CONTRACT = "meeting-bundle.v1"
FORMAT_VERSION = "1.0"
FORMAT_MAJOR = 1

# ── Caps. Refused, never truncated: a partial import is a meeting with holes nobody can see. ────────
MAX_BUNDLE_BYTES = 512 * 1024 * 1024          # the archive as uploaded
MAX_ENTRIES = 2001                             # manifest + 2000 listed files
MAX_TOTAL_UNCOMPRESSED = 1024 * 1024 * 1024    # every entry, inflated
MAX_JSON_PART_BYTES = 32 * 1024 * 1024         # meeting / transcript / annotations / manifest
MAX_WORKSPACE_FILE_BYTES = 16 * 1024 * 1024
MAX_INFLATION_RATIO = 200                      # deflated entries over 1 MiB
_RATIO_FLOOR = 1024 * 1024

MEDIA_CONTENT_TYPES = {
    ("audio", "webm"): "audio/webm",
    ("video", "webm"): "video/webm",
    ("audio", "wav"): "audio/wav",
    ("video", "wav"): "audio/wav",
    ("audio", "mkv"): "audio/x-matroska",
    ("video", "mkv"): "video/x-matroska",
    ("audio", "mp4"): "audio/mp4",
    ("video", "mp4"): "video/mp4",
}

# The path grammar is the schema's EntryPath, compiled once and applied to every entry NAME.
_ENTRY_PATH = re.compile(
    r"^(meeting\.json|transcript\.json|annotations\.json"
    r"|media/[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.(webm|wav|mkv|mp4)"
    r"|workspace/([A-Za-z0-9_][A-Za-z0-9._ -]{0,127}/){0,15}[A-Za-z0-9_][A-Za-z0-9._ -]{0,127})$"
)
_ROLE_FOR = (("meeting.json", "meeting"), ("transcript.json", "transcript"),
             ("annotations.json", "annotations"))
KNOWN_ROLES = frozenset({"meeting", "transcript", "annotations", "media", "workspace"})
# A deterministic archive: every entry carries the same stamp, so the same input is the same bytes.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
# Text is stored as text and rendered as text; what is removed is what no transcript needs and
# what a renderer or a terminal could act on: C0 controls other than tab/newline, DEL and the C1 set.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


class BundleRefused(Exception):
    """One refusal, one contract code, one sentence the caller reads."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail

    def body(self) -> dict:
        return {"code": self.code, "detail": self.detail}


@dataclass
class MediaBlob:
    path: str
    type: str
    format: str
    duration_seconds: Optional[float]
    data: bytes

    @property
    def content_type(self) -> str:
        return MEDIA_CONTENT_TYPES[(self.type, self.format)]


@dataclass
class ParsedBundle:
    manifest: dict
    meeting: dict
    transcript: dict
    annotations: dict
    media: list = field(default_factory=list)          # [MediaBlob]
    workspace: list = field(default_factory=list)      # [(path, bytes)]
    skipped: list = field(default_factory=list)        # roles a newer MINOR added, by path

    @property
    def bundle_id(self) -> str:
        return self.manifest["bundle_id"]


# ── schema ──────────────────────────────────────────────────────────────────────────────────────
_SCHEMA_REL = Path("deploy") / "contracts" / "meeting-bundle.v1" / "meeting-bundle.schema.json"


@lru_cache(maxsize=1)
def _schema() -> dict:
    """The sealed schema, found by walking up from this file: the repository in a checkout, `/app`
    in the image (its Dockerfile copies the schema to the same relative path)."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _SCHEMA_REL
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise RuntimeError(f"meeting-bundle.v1 schema not found by path: {_SCHEMA_REL}")


@lru_cache(maxsize=8)
def _validator(shape: str):
    from jsonschema import Draft202012Validator, FormatChecker

    schema = _schema()
    return Draft202012Validator({"$ref": f"#/$defs/{shape}", "$defs": schema["$defs"]},
                                format_checker=FormatChecker())


def conform(shape: str, doc) -> Optional[str]:
    """The first schema violation of ``doc`` as ``shape``, or ``None`` when it conforms."""
    errors = sorted(_validator(shape).iter_errors(doc), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    e = errors[0]
    where = "/".join(str(p) for p in e.absolute_path) or "(root)"
    return f"{where}: {e.message}"


# ── text hygiene ────────────────────────────────────────────────────────────────────────────────
def clean_text(value, limit: int) -> Optional[str]:
    if value is None:
        return None
    s = _CONTROL.sub("", str(value)).strip()
    return s[:limit]


def _iso(dt) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(doc) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


# ── write ───────────────────────────────────────────────────────────────────────────────────────
def write_bundle(
    *,
    meeting: dict,
    transcript: dict,
    annotations: dict,
    media: "list[MediaBlob]",
    bundle_id: str,
    exported_at: str,
    deployment_id: str,
    source_meeting_id: int,
) -> bytes:
    """The archive for one meeting. Deterministic: the same inputs give the same bytes (entries in a
    fixed order, one fixed timestamp), which is what lets the goldens be byte-for-byte."""
    for shape, doc in (("Meeting", meeting), ("Transcript", transcript), ("Annotations", annotations)):
        reason = conform(shape, doc)
        if reason:
            raise ValueError(f"export produced a non-conforming {shape}: {reason}")
    parts: list = [
        ("meeting.json", "meeting", _canonical(meeting), zipfile.ZIP_DEFLATED),
        ("transcript.json", "transcript", _canonical(transcript), zipfile.ZIP_DEFLATED),
        ("annotations.json", "annotations", _canonical(annotations), zipfile.ZIP_DEFLATED),
    ]
    for blob in media:
        parts.append((blob.path, "media", blob.data, zipfile.ZIP_STORED))
    manifest = {
        "contract": CONTRACT,
        "format_version": FORMAT_VERSION,
        "bundle_id": bundle_id,
        "exported_at": exported_at,
        "source": {"deployment_id": deployment_id, "meeting_id": int(source_meeting_id)},
        "files": [
            {"path": p, "role": r, "bytes": len(b), "sha256": sha256(b).hexdigest()}
            for p, r, b, _ in parts
        ],
    }
    reason = conform("Manifest", manifest)
    if reason:
        raise ValueError(f"export produced a non-conforming Manifest: {reason}")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data, method in [("manifest.json", _canonical(manifest), zipfile.ZIP_DEFLATED)] + [
            (p, b, m) for p, _r, b, m in parts
        ]:
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = method
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.create_system = 3
            zf.writestr(info, data)
    return out.getvalue()


# ── read ────────────────────────────────────────────────────────────────────────────────────────
def _entry_problem(info: zipfile.ZipInfo) -> Optional[tuple]:
    """(code, detail) for an entry that is unsafe by its header alone, else None."""
    name = info.filename
    if name.endswith("/"):
        return "unsafe_entry", f"{name!r}: directory entries are not part of a bundle"
    if (name.startswith("/") or "\\" in name or re.match(r"^[A-Za-z]:", name)
            or any(seg in ("", ".", "..") for seg in name.split("/"))):
        return "unsafe_path", f"{name!r}: a bundle path is relative, forward-slash, with no '.' or '..'"
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG):
        kind = "a symlink" if stat.S_ISLNK(mode) else "not a regular file"
        return "unsafe_entry", f"{name!r} is {kind}"
    if info.flag_bits & 0x1:
        return "unsafe_entry", f"{name!r} is encrypted"
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        return "unsafe_entry", f"{name!r}: compression method {info.compress_type} (stored or deflate only)"
    if name != "manifest.json" and not _ENTRY_PATH.match(name):
        return "unsafe_path", f"{name!r} is not a path a meeting bundle may hold"
    return None


def _cap_for(name: str) -> int:
    if name.endswith(".json") and "/" not in name:
        return MAX_JSON_PART_BYTES
    if name.startswith("workspace/"):
        return MAX_WORKSPACE_FILE_BYTES
    return MAX_TOTAL_UNCOMPRESSED


def _read_bounded(zf: zipfile.ZipFile, info: zipfile.ZipInfo, cap: int) -> bytes:
    """Inflate one entry, never past ``cap`` — the declared size was already checked, and this
    holds even when the header lies about it."""
    out = bytearray()
    try:
        with zf.open(info) as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                out += chunk
                if len(out) > cap:
                    raise BundleRefused("too_large", f"{info.filename!r} inflates past its {cap}-byte cap")
    except BundleRefused:
        raise
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError) as e:
        raise BundleRefused("not_a_bundle", f"{info.filename!r} cannot be read: {e}")
    return bytes(out)


def _media_magic_ok(fmt: str, data: bytes) -> bool:
    if fmt in ("webm", "mkv"):
        return data[:4] == b"\x1a\x45\xdf\xa3"
    if fmt == "mp4":
        return data[4:8] == b"ftyp"
    if fmt == "wav":
        return data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    return False


def _load_json(name: str, data: bytes):
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleRefused("invalid_part", f"{name} is not UTF-8 JSON: {e}")


def read_bundle(raw: bytes) -> ParsedBundle:
    """Validate an archive end to end and return its parts, or raise :class:`BundleRefused`."""
    if len(raw) > MAX_BUNDLE_BYTES:
        raise BundleRefused("too_large", f"the bundle is {len(raw)} bytes (max {MAX_BUNDLE_BYTES})")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        infos = zf.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as e:
        raise BundleRefused("not_a_bundle", f"not a zip archive: {e}")
    if len(infos) > MAX_ENTRIES:
        raise BundleRefused("too_large", f"{len(infos)} entries (max {MAX_ENTRIES})")

    seen: set = set()
    total = 0
    for info in infos:
        problem = _entry_problem(info)
        if problem:
            raise BundleRefused(*problem)
        if info.filename in seen:
            raise BundleRefused("unsafe_entry", f"{info.filename!r} appears twice")
        seen.add(info.filename)
        cap = _cap_for(info.filename)
        if info.file_size > cap:
            raise BundleRefused("too_large", f"{info.filename!r} declares {info.file_size} bytes (max {cap})")
        total += info.file_size
        if (info.compress_type == zipfile.ZIP_DEFLATED and info.file_size > _RATIO_FLOOR
                and info.file_size > MAX_INFLATION_RATIO * max(info.compress_size, 1)):
            raise BundleRefused("too_large", f"{info.filename!r} inflates more than {MAX_INFLATION_RATIO}:1")
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise BundleRefused("too_large", f"the bundle inflates to {total} bytes (max {MAX_TOTAL_UNCOMPRESSED})")
    if "manifest.json" not in seen:
        raise BundleRefused("not_a_bundle", "the archive has no manifest.json")

    by_name = {i.filename: i for i in infos}
    manifest = _load_json("manifest.json", _read_bounded(zf, by_name["manifest.json"], MAX_JSON_PART_BYTES))
    if not isinstance(manifest, dict) or manifest.get("contract") != CONTRACT:
        raise BundleRefused("unsupported_version",
                            f"manifest.json does not declare contract {CONTRACT!r} "
                            f"(got {manifest.get('contract') if isinstance(manifest, dict) else None!r})")
    version = str(manifest.get("format_version") or "")
    major = version.split(".", 1)[0]
    if not major.isdigit() or int(major) != FORMAT_MAJOR:
        raise BundleRefused("unsupported_version",
                            f"format_version {version!r} — this deployment reads {FORMAT_MAJOR}.x only")
    reason = conform("Manifest", manifest)
    if reason:
        raise BundleRefused("invalid_part", f"manifest.json: {reason}")

    listed = {}
    for f in manifest["files"]:
        if f["path"] in listed:
            raise BundleRefused("manifest_mismatch", f"{f['path']!r} is listed twice")
        listed[f["path"]] = f
    present = seen - {"manifest.json"}
    unlisted = sorted(present - set(listed))
    if unlisted:
        raise BundleRefused("manifest_mismatch", f"entries the manifest does not list: {unlisted[:5]}")
    missing = sorted(set(listed) - present)
    if missing:
        raise BundleRefused("manifest_mismatch", f"files the manifest lists but the archive lacks: {missing[:5]}")
    for path, f in listed.items():
        expected = dict(_ROLE_FOR).get(path) or path.split("/", 1)[0]
        if f["role"] != expected:
            raise BundleRefused("manifest_mismatch", f"{path!r} is listed as role {f['role']!r}, not {expected!r}")
    for required in ("meeting.json", "transcript.json"):
        if required not in listed:
            raise BundleRefused("manifest_mismatch", f"a bundle must carry {required}")

    blobs: dict = {}
    for path, f in listed.items():
        data = _read_bounded(zf, by_name[path], _cap_for(path))
        if len(data) != f["bytes"] or sha256(data).hexdigest() != f["sha256"]:
            raise BundleRefused("hash_mismatch", f"{path!r} does not match its manifest length/SHA-256")
        blobs[path] = data

    meeting = _load_json("meeting.json", blobs["meeting.json"])
    transcript = _load_json("transcript.json", blobs["transcript.json"])
    annotations = (_load_json("annotations.json", blobs["annotations.json"])
                   if "annotations.json" in blobs else {"metadata": {}, "notes": None})
    for shape, name, doc in (("Meeting", "meeting.json", meeting),
                             ("Transcript", "transcript.json", transcript),
                             ("Annotations", "annotations.json", annotations)):
        reason = conform(shape, doc)
        if reason:
            raise BundleRefused("invalid_part", f"{name}: {reason}")
    if len(json.dumps(annotations["metadata"], separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > 16 * 1024:
        raise BundleRefused("invalid_part", "annotations.json: metadata exceeds 16 KiB")

    media = []
    declared_media = {m["path"]: m for m in meeting["media"]}
    for path in sorted(p for p in listed if p.startswith("media/")):
        part = declared_media.get(path)
        if part is None:
            raise BundleRefused("manifest_mismatch", f"{path!r} is not declared in meeting.json media")
        if not path.endswith("." + part["format"]):
            raise BundleRefused("unsafe_media", f"{path!r} does not carry its declared format {part['format']!r}")
        if not _media_magic_ok(part["format"], blobs[path]):
            raise BundleRefused("unsafe_media", f"{path!r} is not a {part['format']} container")
        media.append(MediaBlob(path=path, type=part["type"], format=part["format"],
                               duration_seconds=part.get("duration_seconds"), data=blobs[path]))
    undelivered = sorted(set(declared_media) - set(listed))
    if undelivered:
        raise BundleRefused("manifest_mismatch", f"meeting.json declares media the bundle lacks: {undelivered}")

    workspace = sorted((p, blobs[p]) for p in listed if p.startswith("workspace/"))
    return ParsedBundle(manifest=manifest, meeting=meeting, transcript=transcript,
                        annotations=annotations, media=media, workspace=workspace)
