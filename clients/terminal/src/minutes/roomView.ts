/** MINUTES — what a room SHOWS, and which page is in front.
 *
 *  Two decisions, deliberately separated:
 *
 *    the MEETING decides which pages EXIST. There are exactly TWO layouts, not three (founder
 *    ruling): the only distinction that changes the room is whether a TRANSCRIPT EXISTS YET.
 *    `meetingPhase()` still returns prep | live | post — chat.tsx needs all three for its mode
 *    chip — but live and post render the same shape here, because in both a transcript exists
 *    and it belongs on the right. Splitting them over-fits.
 *
 *    the CHAT decides which of them is IN FRONT. `?view=` is not a URL feature: it seeds a chat's
 *    `artifacts[]` — the right-sidebar tabs — and after that the state belongs to the chat. A
 *    deeplink is just a chat constructor. It may focus a page the meeting already has, or add a
 *    file; it may not invent a meeting page that does not exist, and it may not empty the panel.
 *
 *  Both are pure functions on purpose: the shell wires them, the tests read them directly.
 */
import type { MeetingPhase } from "../surfaces/meetingModel";
import type { Page } from "./types";

/** Where App.tsx stashes a `?view=` spec — the URL is cleaned on landing, so the value travels here.
 *  What it carries is a chat's opening `artifacts[]`, not a route. */
export const VIEW_KEY = "vexa.composedView";

/** The reader's own page. Present in every room: there is always somewhere to write. */
export const personalPage = (): Page => ({ path: "README.md", label: "Personal page" });

/** The pages a meeting room shows — TWO shapes, keyed on whether a transcript exists.
 *  `native` absent = nothing captured under this row yet, so there is only the personal page. */
export function pagesForPhase(phase: MeetingPhase, native?: string | null): Page[] {
  if (!native) return [personalPage()];
  const doc = `kg/entities/meeting/${native}.md`;
  const script = `kg/entities/meeting/${native}.transcript.md`;
  // prep: no transcript yet. The one page that matters before the room — what you walk in to decide.
  if (phase === "prep") return [{ path: doc, label: "Brief" }, personalPage()];
  // live or post: a transcript exists and it leads. The meeting doc is the SAME file either way —
  // it is the brief while the room is running and the minutes once it is not, so only its name moves.
  return [
    { path: script, label: "Transcript" },
    { path: doc, label: phase === "post" ? "Minutes" : "Brief" },
    personalPage(),
  ];
}

/** A label reduced to the words a link may name: "Transcript · live" → ["transcript", "live"]. */
const words = (label: string) => label.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

/** Does `name` name this page? The label's FIRST word is its name; anything after is qualifier —
 *  which is why `transcript` finds both "Transcript" and "Transcript · live". */
export function labelMatches(label: string, name: string): boolean {
  const w = words(label);
  return w.length > 0 && w[0] === name;
}

export interface ResolvedView {
  /** the chat's opening artifacts — the meeting's own pages, plus anything `file:` added. */
  pages: Page[];
  /** the artifact the link asked to open, or null — null means "keep the room's default". */
  focus: Page | null;
}

/** Resolve a `?view=` spec into a chat's opening `artifacts[]`.
 *
 *  `transcript` | `minutes` | `brief` — focus that page (matched on the label the room produced)
 *  `file:<path>`                      — add that page and focus it
 *  comma-separated; the last token that RESOLVES keeps focus. Unresolvable names are ignored, so
 *  a stale link degrades to the room's own layout instead of an empty panel. */
export function resolveView(spec: string | null | undefined, phasePages: Page[]): ResolvedView {
  const pages = [...phasePages];
  let focus: Page | null = null;
  for (const raw of (spec ?? "").split(",")) {
    const token = raw.trim();
    if (!token) continue;
    if (/^file:/i.test(token)) {
      const path = token.slice(5).trim().replace(/^\/+/, "");
      // a link never walks out of the mount, and never names an absolute host path
      if (!path || path.split("/").includes("..")) continue;
      const already = pages.find((pg) => pg.path === path && !pg.slug);
      if (already) { focus = already; continue; }
      const added: Page = { path, label: (path.split("/").pop() ?? path).replace(/\.md$/i, "") };
      pages.push(added);
      focus = added;
      continue;
    }
    const hit = pages.find((pg) => labelMatches(pg.label, token.toLowerCase()));
    if (hit) focus = hit;   // no hit → ignored on purpose; the default survives
  }
  return { pages, focus };
}
