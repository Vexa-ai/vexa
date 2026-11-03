/** WHAT A ROW IN THE INBOX OR THE NOW STRIP DOES (feature `shell2`; PRD R11).
 *
 *  - The row's own click OPENS what the item is about — never decides it (R11: a first tap never
 *    performs an irreversible act). A card, a page or a meeting opens in the right panel; a chat in
 *    the centre.
 *  - The item's verb is the one explicit act (`planNeedAct`, the same plan the status cards run).
 *  - Decline exists only where the server has one (a proposal), sits in "⋯" (never under a finger on
 *    touch), and is held for a few seconds behind Undo before it is sent. */
import { OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../../platform/events";
import type { Need } from "../desktop/home2/attentionApi";
import { loadAttention } from "../desktop/home2/attentionApi";
import { openDocOnRight, planNeedAct, resolveProposal } from "../desktop/home2/acts";
import { toast } from "../../ui-kit";

const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));

/** Open what the item is about, deciding nothing. */
export function openNeed(n: Need): void {
  const a = n.act;
  if (a.kind === "open-session" || a.kind === "open-meeting" || a.kind === "open-page" || a.kind === "open-card") { void planNeedAct(a, n.text).run(); return; }
  if (a.path) { openDocOnRight(a.slug, a.path); return; }
  if (a.meeting) { fire(OPEN_MEETING_EVENT, { ref: String(a.meeting) }); return; }
  if (a.session) { fire(OPEN_SESSION_EVENT, { session: a.session }); return; }
}

/** Is the verb the same as opening? Then the row needs no separate verb button's confirm. */
export const verbOpens = (n: Need): boolean => n.act.kind.startsWith("open-");

/** Only a proposal can be declined, and only where it is decided here (a call's proposal is decided
 *  in its chat). */
export const canDecline = (n: Need): boolean => n.act.kind === "accept-proposal" && n.act.source !== "call" && !!n.act.proposal;

/** How long a Decline waits for Undo before it is sent. */
export const UNDO_MS = 5000;

/** Decline with Undo: hidden at once (`onHide`), sent after the Undo window unless undone. */
export function declineWithUndo(n: Need, onHide: (on: boolean) => void): void {
  onHide(true);
  let undone = false;
  const timer = window.setTimeout(() => {
    if (undone) return;
    void resolveProposal(n.act, false).finally(() => { void loadAttention({ force: true }); });
  }, UNDO_MS);
  toast("Declined", { undo: () => { undone = true; window.clearTimeout(timer); onHide(false); } });
}

/** WHO IS BEHIND AN ITEM (founder 2026-10-17: "humans must be visible by name"). The server's own
 *  field when it sends one (`by` on the item or its act), else the name the sentence opens with
 *  ("Maya Hart mentioned you…", "Support desk proposes…"). Nobody, when neither says. */
const OPENS_WITH = /^(.{2,60}?)\s+(?:mentioned you|proposes|asks|asked|wants|shared|invited you|waits on you|is waiting|replied|commented|signed|added you)\b/i;
export function whoOf(n: Need): string {
  const by = (n as { by?: unknown }).by ?? n.act.by;
  if (typeof by === "string" && by.trim()) return by.trim();
  const m = OPENS_WITH.exec(n.text.trim());
  return m ? m[1].trim() : "";
}
