"use client";
/** THE ARRIVAL — the handover between "a `?s=` link was clicked" and "a chat is on screen".
 *
 *  Two components share that moment and neither can do the other's half: `app/App.tsx` is the only
 *  thing that sees the URL, `minutes/MinutesShell.tsx` is the only thing that owns the chat list.
 *  So an id travels from one to the other — and the handover, not either half, is what broke.
 *
 *  WHAT BROKE (2026-09-05, on the dogfood stack). The writer cleaned `?s=` off the URL with
 *  `location.replace(pathname)`, which is a NAVIGATION. React runs a child's effects before its
 *  parent's, so by the time that fired the reader had already taken the id OUT of storage and put a
 *  `GET /api/scaffolds/<id>` in flight. The navigation aborted the request; the second document
 *  found nothing pending; neither the chat nor the refusal card ever rendered. A real first-time
 *  invitee landed on an empty "New chat" with a proposal chip on it. The server was right the whole
 *  time — it answered, and the client threw its own answer away.
 *
 *  THE CONTRACT IS ONE LINE: every `?s=` arrival ends in a rendered state — the chat it names, or
 *  the card saying why not — IN THE DOCUMENT IT LANDED IN. Three rules hold it up, and each is that
 *  same line seen from a different side:
 *
 *   1. **Clean the URL without navigating.** A reload must not re-open a spent arrival, which is
 *      why the parameter is stripped at all; nothing about that needs a second document, and asking
 *      for one destroys the first one's work in flight. `history.replaceState` rewrites this
 *      document's own entry: no request, no unload, nothing aborted.
 *   2. **Reading the id is not settling it.** The id leaves storage when the fetch has ANSWERED, so
 *      a reader that consumes it and then dies has not thrown the arrival away with nothing to show
 *      for it.
 *   3. **Either order is correct.** The reader looks at the stash on mount AND waits to be told, so
 *      it does not matter which effect ran first. The announcement carries no payload — it only
 *      says *look now* — because storage stays the one transport, exactly as §5.5 has it: the link
 *      carries an id, and the id travels by storage.
 */
import { useEffect, useRef } from "react";
import { fetchScaffold, type Scaffold, type ScaffoldRefusal } from "./scaffold";

/** Where the id waits. ONE name in ONE file: it was typed as a literal in both halves of the
 *  handover, which is two owners for one value and the shape every silent drift in this codebase
 *  has had. */
export const PENDING_SCAFFOLD = "vexa.pendingScaffold";

/** The parameter an arrival comes in on. */
const PARAM = "s";

const watchers = new Set<() => void>();

/** Wait to be told an arrival was stashed. Returns the unsubscribe. */
export function onArrival(fn: () => void): () => void {
  watchers.add(fn);
  return () => { watchers.delete(fn); };
}

/** What is pending — READ ONLY. Consuming it is `resolveArrival`'s job and happens on the answer
 *  (rule 2); a reader that took it here would be settling an arrival it has not yet made. */
export function pendingArrival(): string | null {
  try { return localStorage.getItem(PENDING_SCAFFOLD); } catch { return null; }
}

/** `?s=` out of the address bar and NOTHING else touched — no navigation, and no opinion about the
 *  other parameters. `?meeting=` and `?ask=` clean themselves by RELOADING, on purpose, so their
 *  stash is in place before the grid mounts; that is their semantics to keep, and only this one
 *  parameter is removed here. */
export function cleanArrivalParam(): void {
  try {
    const url = new URL(window.location.href);
    if (!url.searchParams.has(PARAM)) return;
    url.searchParams.delete(PARAM);
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  } catch { /* a history the browser will not let us rewrite is not worth losing the arrival over */ }
}

/** The WRITER's half: stash the id, clean the URL in place, then say *look now*.
 *
 *  Safe to call twice with the same id — the stash is idempotent, the clean is a no-op once the
 *  parameter is gone, and the reader fires once per mount. */
export function beginArrival(id: string): void {
  try { localStorage.setItem(PENDING_SCAFFOLD, id); } catch { /* locked-down storage */ }
  cleanArrivalParam();
  for (const w of [...watchers]) {
    try { w(); } catch (e) { console.error("scaffold arrival watcher threw:", e); }
  }
}

/** The READER's half, minus the rendering: fetch the record, and settle the stash ON THE ANSWER.
 *
 *  `unavailable` is the one outcome that KEEPS the id, and that is rule 2 rather than an exception
 *  to it: `refusalCopy` tells that reader *"Nothing is lost — reload in a moment and it will
 *  open"*, and the URL no longer carries the id, so the stash is the only thing left that can make
 *  the sentence true. Every other answer is final — a 404, a 403 and an unreadable body do not
 *  become something else on a reload — and a scaffold that opened is spent. */
export async function resolveArrival(
  id: string,
  fetcher: typeof fetch = fetch,
): Promise<{ ok: true; scaffold: Scaffold } | { ok: false; refusal: ScaffoldRefusal }> {
  const got = await fetchScaffold(id, fetcher);
  if (got.ok || got.refusal.reason !== "unavailable") {
    try { localStorage.removeItem(PENDING_SCAFFOLD); } catch { /* ignore */ }
  }
  return got;
}

/** The READER, wired: at most one arrival per mount, whichever effect ran first, ending in exactly
 *  one of the two states the contract allows.
 *
 *  It looks at the stash immediately AND subscribes, so it can be a child of the component that
 *  writes the stash without depending on running second (rule 3).
 *
 *  IT DOES NOT CANCEL ON UNMOUNT, deliberately. An arrival that has already asked the server must
 *  land: a StrictMode remount would otherwise consume the id on the first mount and drop the answer
 *  on the second — the same defect as the reload, reached from inside React instead of from the
 *  address bar. */
export function useScaffoldArrival(on: {
  onOpen: (s: Scaffold) => void;
  onRefuse: (r: ScaffoldRefusal) => void;
}): void {
  const handlers = useRef(on);
  useEffect(() => { handlers.current = on; });
  const fired = useRef(false);
  useEffect(() => {
    const attempt = () => {
      if (fired.current) return;
      const id = pendingArrival();
      if (!id) return;
      fired.current = true;
      void (async () => {
        const got = await resolveArrival(id);
        if (!got.ok) {
          console.error("scaffold " + id + " did not open:", got.refusal.reason, got.refusal.detail);
          handlers.current.onRefuse(got.refusal);
          return;
        }
        handlers.current.onOpen(got.scaffold);
      })();
    };
    const off = onArrival(attempt);
    attempt();
    return off;
  }, []);
}
