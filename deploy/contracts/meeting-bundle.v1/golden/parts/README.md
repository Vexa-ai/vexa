# golden/parts — the parts archive an export takes

The agent domain's half of a bundle, as its owner's client hands it to meeting-api's
`POST /meetings/{meeting_id}/export`: `workspace/**` entries and `notes.md`, no manifest.

- `workspace-and-notes.zip` — must be accepted (two workspace files and a meeting page).
- the zips named in [`refused.json`](refused.json) — must be refused with the code it names.
