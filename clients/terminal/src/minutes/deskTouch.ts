/** deskTouch — the panel tells the desk which cards its person actually opens.
 *
 *  Founder, 2026-09-02: the desk README is *"the thing where they have what they generally need —
 *  mostly links to the other cards in different workspaces."* A list of links is only useful if the
 *  ones this person uses are at the top, and nothing else in the system knows which those are:
 *  ranking by last-modified ranks by what the AGENT wrote, which is close to the opposite — the page
 *  it just wrote is the one they have not read yet.
 *
 *  So the view slot reports what it opened and agent-api keeps it per desk id. Deliberately:
 *
 *    FIRE AND FORGET. A failure is swallowed. The panel is rendering a document the person asked
 *    for; a usage signal is never worth a spinner, an error, or a millisecond of that.
 *
 *    DE-DUPED PER SESSION. Re-opening the tab already in front is not a second use, and a tab strip
 *    that re-renders would otherwise report the same page a dozen times a minute.
 *
 *    IT SENDS A WORKSPACE ID, never a slug. The desk README links by id, so the touch has to be
 *    keyed the same way or it can never be matched back to the card it names — and an id survives
 *    the workspace being renamed, which a slug does not.
 */
import { workspaceBySlug } from "../ui-kit/wsLinks";

const seen = new Map<string, number>();
/** Long enough that a tab strip re-render is silent; short enough that coming back to a page later
 *  in the same session still counts as coming back to it. */
const REPEAT_MS = 60_000;

/** Report that this page was opened. Never throws, never awaits anything the caller needs. */
export function reportOpened(slug: string | undefined, path: string): void {
  const p = (path ?? "").trim();
  if (!p) return;
  void (async () => {
    try {
      // No slug = the reader's own desk. Its id is what the README links by, so it is resolved the
      // same way every other workspace's is rather than special-cased here.
      const api = await import("../surfaces/workspaceApi");
      const own = slug ?? (await api.readActiveSet()).subject;
      if (!own) return;
      const rec = await workspaceBySlug(String(own));
      if (!rec?.id || rec.access !== "readable") return;
      const key = `${rec.id}/${p}`;
      const at = Date.now();
      if (at - (seen.get(key) ?? 0) < REPEAT_MS) return;
      seen.set(key, at);
      await api.touchDeskPage(rec.id, p);
    } catch {
      /* a usage signal is never worth an error to somebody reading a document */
    }
  })();
}

/** Testing seam: forget what this session has already reported. */
export function resetReportedPages(): void {
  seen.clear();
}
