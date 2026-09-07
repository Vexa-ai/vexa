# /api/workspace/asset

The BYTES a page's `![…](assets/…)` renders (Vexa-ai/vexa#1612) — GET reads one asset through the
gateway, POST fetches a remote image into the workspace, PUT stores one a person dropped or pasted.

Its own route rather than a case in the sibling `[...seg]/` because that handler reads every upstream
answer with `await upstream.text()` and stamps `application/json` on it: correct for JSON, data loss
for a PNG. A static App-Router segment beats the sibling catch-all, so this file owns
`/api/workspace/asset` whole — which is why all three methods are exported here.
