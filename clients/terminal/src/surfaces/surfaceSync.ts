"use client";
/** THE HUMAN SURFACE, WRITTEN TO THE SESSION RECORD (PRD decision 30).
 *
 *  What the person is looking at is a FACT the server should hold, not something the client
 *  re-describes in every prompt. Today the terminal prefixes "Active context: the user is viewing
 *  the workspace file …" into the user's own message — the agent reads the human's words with our
 *  narration stapled to the front, and the fact exists only for the duration of one turn.
 *
 *  So: the terminal PUTs the whole surface — which chat, which meeting and phase, what is in the
 *  view, the strip's history and pins, the navigator — whenever any of it changes. The server holds
 *  it; the prompt stops carrying it.
 *
 *  THREE PROPERTIES, each because the alternative is worse:
 *
 *    · DEBOUNCED (~300ms). Navigation is bursty — opening a folder walks several pages — and one
 *      PUT per keystroke-speed change would be a write storm of exactly the kind that turned a
 *      gateway close-loop into 519 requests in three minutes this morning.
 *    · FIRE-AND-FORGET. It never blocks the UI and never throws. A surface the server did not
 *      record is a worse agent turn; a surface that *stalled the panel* is a broken product.
 *    · BEHIND A FLAG. The route is another worker's (stage-1 owns it and will confirm the field
 *      names). Until it lands, `syncSurface` is inert and the prompt KEEPS its prefix — because
 *      dropping the narration before the server fact exists would leave the agent knowing less than
 *      it does today. One flag flips both halves together, which is the only safe way to trade one
 *      mechanism for another.
 */

/** Is the server-side surface record live? Flip when stage-1's route lands and the field names are
 *  confirmed. Until then: the PUT is a no-op and the prompt keeps its "Active context" prefix. */
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
 *  state nobody stopped on is not worth a request. */
export function syncSurface(
  session: string,
  surface: Surface,
  opts: { fetcher?: typeof fetch; debounceMs?: number } = {},
): void {
  if (!SURFACE_RECORD_LIVE || !session) return;
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

/** Read the surface the server holds for a session, or null. Used on load: when the LOCAL strip is
 *  empty and the server has one, the server's wins — that is the case where this record earns its
 *  keep (a new browser, a cleared store, another device). A local strip is never overwritten: the
 *  reader's own machine knows what they were just doing. */
export async function readSurface(session: string, fetcher: typeof fetch = fetch): Promise<Surface | null> {
  if (!SURFACE_RECORD_LIVE || !session) return null;
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
 *  agent could be told two different things about the same screen. */
export const promptCarriesActiveContext = (): boolean => !SURFACE_RECORD_LIVE;
