# bundle — a meeting as a portable file (meeting-bundle.v1)

Export a meeting its owner holds as one zip any other Vexa deployment can import, and import such a
zip as a new meeting owned by the importer. The format is the sealed contract
`deploy/contracts/meeting-bundle.v1`; user and operator page: `docs/docs/how-to/move-a-meeting.mdx`
(including *How it works* and *Why it complies*).

## Front door (`__init__.py`)
- `build_router(store, recording_repo, storage, finalize=, log_event=, secret=)` —
  `GET`/`POST /meetings/{meeting_id}/export[?media=false]` (POST takes the agent domain's parts
  archive: the workspace tree and the meeting's page) and `POST /meetings/import[?dry_run=true]`,
  mounted by `meeting_api.app.create_app`.
- `write_bundle` / `read_bundle`, `write_parts` / `read_parts` (`codec.py`) — the pure codec, vendored
  VERBATIM from `deploy/contracts/meeting-bundle.v1/bundle_codec.py` with the schema beside it
  (`gate:fact-parity`); every refusal is a `BundleRefused` carrying one contract code.
- `export_meeting` / `import_bundle` (`service.py`) — the flows over the ports meeting-api already
  owns: the transcript store, the recording repo and object storage.

## How it works
- **Export:** owner check through the store's access union → segments re-timed to seconds from the
  meeting start → each recording's master finalized and read → `write_bundle` validates every part
  against the schema, hashes it, and writes a deterministic zip.
- **Import:** body read under a cap → `read_bundle` (entry names and headers, sizes and inflation,
  manifest and version, listing, hashes, schemas, media signatures) → duplicate check on
  `imported_bundle_id` → planned row (auto-join off) → recordings into storage under the importer's
  own prefix + `data.recordings` → annotations and notes with `imported_from` provenance → the transcript
  through the transcript-import write, which completes the row. A failure before completion deletes
  the row and the objects written for it. The workspace and the page are named in the preview's
  `handoff`; the agent domain restores them (`POST /agent/meeting/bundle-restore`).

## Depends on
`collector.transcript_import` (segment normalization, session uid), `recordings` (front door:
`finalize_master`, `new_recording_numeric_id`), the sealed schema found by walking up to
`deploy/contracts/meeting-bundle.v1/meeting-bundle.schema.json` (the image copies it there), and
`jsonschema`. Nothing outside meeting-api.

Tests: `tests/test_meeting_bundle.py` (two deployments, round trip, deny), `tests/test_meeting_bundle_contract.py`
(the goldens, both directions), fixtures in `tests/bundle_goldens.py`.
