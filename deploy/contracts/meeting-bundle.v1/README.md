# meeting-bundle.v1 — one meeting as a portable file

A **meeting bundle** is one zip archive holding everything a separate Vexa deployment needs to
recreate a meeting: its metadata, its transcript, its annotations and, optionally, its recordings.
It carries nothing that only makes sense on the deployment it came from. This README is the format
for anyone producing or reading bundles, inside Vexa or not; the schema is
[`meeting-bundle.schema.json`](meeting-bundle.schema.json) and the goldens in [`golden/`](golden/)
are the spec (P8). Product page: [Move a meeting between deployments](../../../docs/docs/how-to/move-a-meeting.mdx).

## Layout

```
manifest.json          required — what is inside, with a SHA-256 per file
meeting.json           required — #/$defs/Meeting
transcript.json        required — #/$defs/Transcript
annotations.json       optional — #/$defs/Annotations (absent ≡ {"metadata": {}, "notes": null})
media/<name>.<fmt>     optional — recordings; fmt ∈ webm · wav · mkv · mp4
workspace/<path>       optional — files of the meeting's workspace, as a tree
```

`manifest.json`:

```json
{
  "contract": "meeting-bundle.v1",
  "format_version": "1.0",
  "bundle_id": "6f1c2b9e-3d4a-4c5b-8e7f-0a1b2c3d4e5f",
  "exported_at": "2026-10-10T12:00:00Z",
  "source": {"deployment_id": "<32 hex>", "meeting_id": 4242},
  "files": [{"path": "meeting.json", "role": "meeting", "bytes": 403, "sha256": "<64 hex>"}, "…"]
}
```

- **`files` lists every entry except `manifest.json`, and nothing else.** `role` is fixed by the path:
  `meeting.json` → `meeting`, `transcript.json` → `transcript`, `annotations.json` → `annotations`,
  `media/…` → `media`, `workspace/…` → `workspace`.
- **`bundle_id`** is minted per export; importers use it to detect a re-import.
- **`source`** is provenance only — an opaque deployment id (stable per deployment, not an address)
  and the meeting's id there. An importer never reuses either as an id.
- Times are ISO-8601 UTC. Transcript `start`/`end` are seconds from `meeting.start_time`.
- `meeting.media[]` declares each `media/` file (`type` audio|video, `format`, `duration_seconds`).
- `participants` are display names only.
- Every object is closed (`additionalProperties: false`): a field the schema does not name — a user
  id, a storage path, a URL — makes the part invalid. That is how "nothing deployment-bound" is held.

## Versioning

`format_version` is `MAJOR.MINOR`. An importer refuses a MAJOR it does not know
(`unsupported_version`). The schema is sealed in `contracts.seal.json`: a breaking change is a new
directory (`meeting-bundle.v2`); a back-compatible one is a reviewed re-seal.

## Importing a bundle safely

An importer refuses — before writing anything — a bundle that breaks any rule below, answering one
`Refusal` code:

| Rule | Code |
|---|---|
| a zip with a `manifest.json` | `not_a_bundle` |
| `contract` is `meeting-bundle.v1` and the MAJOR is known | `unsupported_version` |
| every entry name is relative, forward-slash, no `.`/`..` segment, no drive letter, and matches the schema's `EntryPath` | `unsafe_path` |
| no symlinks or other special entries, no encrypted entries, stored/deflate only, no duplicate names, no directory entries | `unsafe_entry` |
| caps: 512 MiB uploaded, 1 GiB inflated, 32 MiB per JSON part, 16 MiB per workspace file, 2,001 entries, deflate ratio ≤ 200:1 above 1 MiB — checked on the declared sizes and again while inflating | `too_large` |
| the archive and `files` list the same entries; roles agree with paths; `meeting.json` and `transcript.json` are present; declared media are present | `manifest_mismatch` |
| every file's length and SHA-256 equal the manifest's | `hash_mismatch` |
| every part conforms to its schema shape (metadata ≤ 16 KiB serialized) | `invalid_part` |
| each media file starts with its container's signature (EBML for webm/mkv, `RIFF…WAVE`, `ftyp` for mp4) | `unsafe_media` |
| (Vexa) the importer has not already imported this `bundle_id` | `duplicate_import` |

Never join an entry name onto a filesystem path, and store recordings with a content type chosen
from the declared format, not from the file. Render all text as text.

## Testing your own importer or exporter

- [`golden/bundles/`](golden/bundles/) — bundles every importer must accept: `transcript-only.zip`
  and `with-audio.zip`.
- [`golden/refused/`](golden/refused/) — malicious or malformed bundles, and
  [`refused.json`](golden/refused/refused.json), the code each must be refused with.
- `node validate.mjs --file my.zip` — this directory's reference reader prints `ACCEPT` or `REFUSE
  <code>` for any bundle (Node ≥ 20, `ajv` + `ajv-formats`). It reads the zip with no archive library.

## Who implements it

meeting-api's `meeting_api/bundle` module is the exporter (`GET /meetings/{meeting_id}/export`) and
the importer (`POST /meetings/import`). `core/meetings/services/meeting-api/tests/test_meeting_bundle_contract.py`
holds both to these goldens: the exporter's output is byte-for-byte `golden/bundles/transcript-only.zip`,
and the importer refuses every refused golden with the code `validate.mjs` gives it.

Enforced by `gate:schema` (`validate.mjs --check`) and `gate:contract-version` (the seal).
