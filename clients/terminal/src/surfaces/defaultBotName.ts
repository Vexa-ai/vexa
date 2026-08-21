/** Default bot name the terminal sends to the API when joining meetings.
 *
 *  RUNTIME (no rebuild): the deployment sets `DEFAULT_BOT_NAME` in the container env; the terminal's
 *  `/api/config` route exposes it, and we cache it here. This is the wiring that lets a plain compose
 *  var take effect on restart — Next.js inlines `NEXT_PUBLIC_*` at BUILD time, so it can't.
 *
 *  Precedence (call-time): the cached runtime `DEFAULT_BOT_NAME` → `NEXT_PUBLIC_DEFAULT_BOT_NAME`
 *  (build-time fallback) → `"Vexa"`. `defaultBotName()` stays SYNC so every join call site is
 *  unchanged; `loadDefaultBotName()` warms the cache (fired at module load in the browser) so the
 *  value is ready before the first join.
 */
let cached: string | null = null;
let inflight: Promise<void> | null = null;

/** Warm the runtime cache from `/api/config`. Idempotent, browser-only, best-effort (a failure just
 *  leaves the fallbacks in place). Returns a promise so a caller may await it before a join. */
export function loadDefaultBotName(): Promise<void> {
  if (cached !== null) return Promise.resolve();
  if (inflight) return inflight;
  if (typeof fetch !== "function") return Promise.resolve();
  inflight = fetch("/api/config", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((cfg: { defaultBotName?: string | null } | null) => {
      const name = (cfg?.defaultBotName ?? "").trim();
      if (name) cached = name;
    })
    .catch(() => { /* best-effort — the fallbacks below still apply */ })
    .finally(() => { inflight = null; });
  return inflight;
}

export function defaultBotName(): string {
  return cached || process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME?.trim() || "Vexa";
}

/** Test-only: clear the module cache so a spec can drive the runtime path deterministically. */
export function __resetDefaultBotNameCache(): void {
  cached = null;
  inflight = null;
}

// Warm as early as possible in the browser so the value is ready before the first join.
if (typeof window !== "undefined") void loadDefaultBotName();
