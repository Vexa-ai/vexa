# `/api/config` — runtime deployment config

`GET /api/config` → `{ defaultBotName: string | null }`, read from the container env **at request
time** (`dynamic = "force-dynamic"`).

Next.js inlines `NEXT_PUBLIC_*` at build time, so a per-deployment default baked into the image
can't change without a rebuild. This route exposes the values the browser needs at **runtime**, so a
plain compose env var takes effect on restart — no rebuild.

- **`DEFAULT_BOT_NAME`** — the meeting bot's display name the terminal sends when joining. The client
  caches this via [`surfaces/defaultBotName.ts`](../../../surfaces/defaultBotName.ts); precedence is
  the runtime value → `NEXT_PUBLIC_DEFAULT_BOT_NAME` (build-time) → `"Vexa"`.
