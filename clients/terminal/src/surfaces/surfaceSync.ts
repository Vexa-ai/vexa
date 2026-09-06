"use client";
/** THE HUMAN SURFACE, WRITTEN TO THE SESSION RECORD (PRD decision 30) — **NOT SHIPPED**.
 *
 *  What the person is looking at is a FACT the server should hold, not something the client
 *  re-describes in every prompt. Today the terminal prefixes "Active context: the user is viewing
 *  the workspace file …" into the user's own message — the agent reads the human's words with our
 *  narration stapled to the front, and the fact exists only for the duration of one turn.
 *
 *  So the design: the terminal PUTs the whole surface — which chat, which meeting and phase, what
 *  is in the view, the strip's history and pins, the navigator — whenever any of it changes. The
 *  server holds it; the prompt stops carrying it.
 *
 *  ⚠ THE SERVER HALF DOES NOT EXIST. `PUT`/`GET /api/sessions/<id>/surface` is in no service in
 *  this repo — agent-api serves `GET /api/sessions` and `GET /api/sessions/<id>/history` and
 *  nothing else under that prefix. So decision 30 is NOT shipped, this module is inert, and the
 *  prompt's "Active context" narration is the ONLY thing telling the agent what the reader has
 *  open. This paragraph is the claim; `SURFACE_RECORD_LIVE` is the claim in code; the test file
 *  pins the two together so they cannot drift apart silently.
 *
 *  THE GATE IS A PARAMETER, NOT A MODULE CONSTANT THE CODE READS BEHIND YOUR BACK. Every function
 *  here takes `live` and defaults it to `SURFACE_RECORD_LIVE`, for one reason: the first version of
 *  this file read the constant directly, so no test could exercise the live path, and its own tests
 *  were green for EITHER value of the flag — flipping it to `true` left all 1110 client tests green
 *  while the PUT went to a 404 and the prompt lost its prefix (2026-09-02 review, R-C09 / R-C10).
 *  A gate that does nothing is worse than an absent one, because it is counted.
 *
 *  THREE PROPERTIES, each because the alternative is worse:
 *
 *    · DEBOUNCED (~300ms). Navigation is bursty — opening a folder walks several pages — and one
 *      PUT per keystroke-speed change would be a write storm of exactly the kind that turned a
 *      gateway close-loop into 519 requests in three minutes this morning.
 *    · FIRE-AND-FORGET. It never blocks the UI and never throws. A surface the server did not
 *      record is a worse agent turn; a surface that *stalled the panel* is a broken product.
 *    · BEHIND ONE GATE. The record and the narration are two answers to one question, so one value
 *      flips both halves together — the only safe way to trade one mechanism for another.
 */

/** Is the server-side surface record live?
 *
 *  **`false`, and flipping it is a release decision, not an edit.** One line does two irreversible
 *  things: it starts PUTting to a route that answers 404, and it DROPS the "Active context"
 *  narration, leaving the agent knowing less than it does today. What must be true first is
 *  written out in `__tests__/surfaceSync.test.ts` — the test that fails the moment this value and
 *  that claim disagree. */
export const SURFACE_RECORD_LIVE = false;

export interface SurfaceRef { workspace: string; path: string; title: string }
export interface SurfaceHistoryEntry extends SurfaceRef { at: number }

export interface Surface {
  chat: { id: string; kind: string };
  meeting: { id: string; phase: string | null } | null;
  view: SurfaceRef | null;
  strip: { history: SurfaceHistoryEntry[]; pins: SurfaceRef[] };
  navigator: { open: boolean; workspace: string | null };
}

type Timer = ReturnType<typeof setTimeout>;
const pending = new Map<string, { timer: Timer; surface: Surface }>();
const DEBOUNCE_MS = 300;

/** Exposed for tests: what a PUT would carry, without waiting on a timer. */
export function surfaceBody(s: Surface): string {
  return JSON.stringify(s);
}

async function put(session: string, surface: Surface, fetcher: typeof fetch): Promise<void> {
  try {
    await fetcher(`/api/sessions/${encodeURIComponent(session)}/surface`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: surfaceBody(surface),
    });
  } catch {
    // deliberately silent: the surface is an optimisation for the NEXT turn, and a failed write
    // must never reach the reader. It is re-sent on their next navigation anyway.
  }
}

/** Record the surface. Coalesces bursts per session and keeps only the latest — an intermediate
 *  state nobody stopped on is not worth a request. Inert unless `live`. */
export function syncSurface(
  session: string,
  surface: Surface,
  opts: { fetcher?: typeof fetch; debounceMs?: number; live?: boolean } = {},
): void {
  const live = opts.live ?? SURFACE_RECORD_LIVE;
  if (!live || !session) return;
  const fetcher = opts.fetcher ?? fetch;
  const wait = opts.debounceMs ?? DEBOUNCE_MS;
  const prev = pending.get(session);
  if (prev) clearTimeout(prev.timer);
  const timer = setTimeout(() => {
    const held = pending.get(session);
    pending.delete(session);
    void put(session, held?.surface ?? surface, fetcher);
  }, wait);
  pending.set(session, { timer, surface });
}

/** Read the surface the server holds for a session, or null. Designed for load: when the LOCAL
 *  strip is empty and the server has one, the server's wins — that is the case where this record
 *  earns its keep (a new browser, a cleared store, another device). A local strip is never
 *  overwritten: the reader's own machine knows what they were just doing.
 *
 *  ⚠ NO PRODUCTION CALLER TODAY (review R-C17): the load-time merge this describes is not
 *  implemented, and it is one of the things that must land before `SURFACE_RECORD_LIVE` may flip. */
export async function readSurface(
  session: string,
  fetcher: typeof fetch = fetch,
  live: boolean = SURFACE_RECORD_LIVE,
): Promise<Surface | null> {
  if (!live || !session) return null;
  try {
    const r = await fetcher(`/api/sessions/${encodeURIComponent(session)}/surface`, { cache: "no-store" });
    if (!r.ok) return null;
    const body = await r.json() as Partial<Surface> | null;
    if (!body || typeof body !== "object" || !body.strip) return null;
    return body as Surface;
  } catch {
    return null;
  }
}

/** Does the prompt still need to narrate what the reader is looking at?
 *
 *  The narration and the record are two answers to one question, and running both would mean the
 *  agent could be told two different things about the same screen. Exactly one is live, always —
 *  which is why this reads the same gate and takes it the same way. */
export const promptCarriesActiveContext = (live: boolean = SURFACE_RECORD_LIVE): boolean => !live;
