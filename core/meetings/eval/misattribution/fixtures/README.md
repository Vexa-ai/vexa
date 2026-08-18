# fixtures

[`manifest.json`](manifest.json) — the production-derived speaker-misattribution
fixture set, conforming to [`../manifest.schema.json`](../manifest.schema.json).

Content-free by contract: meeting ids, virtual-track keys, segment ids, signal
types, verdicts, span *lengths* and tape pointers. No transcript text, no real
participant names — pseudonyms are meeting-local and the map that produces them
never leaves the operator's machine.

`gold` fixtures are corroborated by the session tape (csrc separation + roster
timing replay); `silver` fixtures rest on the linguistic signal alone, either
because no tape survives or because the lane records no cross-check signal.

Rebuild with `build_manifest.py`. The judge is label-blind and the scorer is
deterministic, so a rebuild from stored judge responses reproduces this file
exactly.
