# carve/overrides/ — carve-owned replacement files

Files here are copied **over** the monorepo's equivalents after the open-core
subset is materialized, replacing them in the published `Vexa-ai/vexa-core`
tree. They exist because the carve's shape differs from the mono's: the mono
carries surfaces (lite mode, commercial clients) that the open-core repo does
not ship, so a handful of root files need a carve-specific version.

The mapping lives in [`../manifest.sh`](../manifest.sh) under `CARVE_OVERRIDES`
(`<file-here>:<dest-in-carve>`); add an entry there when you add a file here.

| File | Replaces | Why |
|---|---|---|
| `Makefile` | root `Makefile` | Compose-only entrypoint (`make all` / `down`). The mono's root Makefile references `deploy/lite`, which is excluded from the carve. |

Keep this layer minimal — prefer fixing the source in the mono so the carve
stays a faithful subset. Use an override only when the file genuinely must
differ between the two repos.
