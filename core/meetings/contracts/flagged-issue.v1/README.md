# flagged-issue.v1

A **flagged transcript/attribution bug** — the record that turns a live meeting defect into a
reproducible OFFLINE test (O-TEL-3). A **user** OR the **system** raises one (`flagged_by ∈
{system|human|feedback-api}`); it carries the locator to find the offending segment
(platform/native/session/segment/speaker/text/start/end), a `signal` link to the
[captured-signal.v1](../captured-signal.v1) / legacy tape the raw signal lives in, and a `status`
(`open|investigating|fixed|wontfix`).

`issue_type ∈ {mis-attribution|oversegment|lost-content|hallucination|other}` — derived from what
[`eval/src/analyze.mjs`](../../eval/src/analyze.mjs) already detects: its mis-attribution oracle
(content self-IDs a speaker ≠ the label) and its oversegmentation oracle (mid-utterance cuts /
boundary-word dups). `analyze.mjs --flag-issues` emits conforming records automatically; the
in-memory flag store (`eval/src/flag-store.mjs`) is the loop a `flag(issue) → store → surface on
the system queue → route the signal link to the O-TEL-2 replay`.

The optional **`trace_id`** is the meeting's distributed trace (the same
[logevent.v1](../../../gateway/contracts/logevent.v1) id threaded across the control plane): it pulls
**every** structured log across the system for that meeting AND links to the
[captured-signal.v1](../captured-signal.v1) header carrying the same id — so a flagged bug routes to
both its full cross-system trace and its deterministic replay. `analyze.mjs --flag-issues` stamps it
from `FLAG_TRACE` (the bot's `X-Trace-Id` at capture time). This is the **O-OBS-1 ↔ O-TEL-3** join:
"trace bugs precisely" and "replay and fix" become one key, not two disconnected loops.

`gate:schema` validates goldens ≡ schema. **UNSEALED** (in development) — not yet frozen in
`contracts.seal.json`.
