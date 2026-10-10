# golden/bundles — must be accepted

- `transcript-only.zip` — meeting, transcript (three segments, one carrying literal `<b>` markup that
  must stay text), annotations; no media. Byte-for-byte what meeting-api's exporter writes for the
  fixture meeting in `tests/bundle_goldens.py`.
- `with-audio.zip` — the same plus one WebM audio part declared in `meeting.json`.

Use them to test your own importer: both must import, and the transcript must read back unchanged.
