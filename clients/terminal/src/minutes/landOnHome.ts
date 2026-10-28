/** WHERE OPENING THE APP LANDS (JTBD-1, home2). The home page — workspace cards, "+ New workspace" —
 *  is what a person sees when they open the app, not whichever chat they touched last. An address
 *  that names something (a page link, a query, a hash) or a link the boot stashed before cleaning the
 *  URL still opens that thing: home is the default, never an override. */

/** The keys `App.tsx` stashes a link's target under before it cleans the address bar. */
export const DEEP_LINK_KEYS = [
  "vexa.assignMeeting", "vexa.composedView", "vexa.pendingPreset", "vexa.openMeetingRef",
  "vexa.openWorkspace", "vexa.openMeeting",
] as const;

export function stashedDeepLink(extra: readonly string[] = []): boolean {
  try { return [...DEEP_LINK_KEYS, ...extra].some((k) => localStorage.getItem(k) != null); } catch { return false; }
}

export function landsOnHome(p: { home2: boolean; pathname: string; search: string; hash: string; deepLinked: boolean }): boolean {
  if (!p.home2 || p.deepLinked) return false;
  if (p.pathname && p.pathname !== "/") return false;
  return !p.search && !p.hash;
}
