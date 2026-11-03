/** THE INBOX, as a pure read of what the server already says needs the person (feature `shell2`).
 *
 *  The source is the attention read model (`POST /api/desktop/attention`, decision E1): proposals,
 *  mentions, cards, failed routines and upcoming calls, composed by agent-api from their owners. The
 *  Inbox adds no store and decides nothing: it groups those items and counts them, so the sidebar
 *  badge, the Inbox header and its groups always agree (one read model). */
import type { Need, NeedKind } from "../desktop/home2/attentionApi";

export type InboxGroup = { key: string; label: string; needs: Need[] };

/** Group order and words (sentence case, UI 7x). `upcoming` is not waiting on anyone: it is shown
 *  last, as "Coming up", and is not counted. */
const GROUPS: { key: string; label: string; kinds: NeedKind[] }[] = [
  { key: "waiting", label: "Waiting on you", kinds: ["proposal", "card"] },
  { key: "mentions", label: "Mentions", kinds: ["mention"] },
  { key: "failed", label: "Did not run", kinds: ["failed"] },
  { key: "upcoming", label: "Coming up", kinds: ["upcoming"] },
];

/** Needs the person has not hidden (a muted or hidden workspace never reaches the Inbox badge). */
export function inboxNeeds(needs: readonly Need[], quiet: (slug: string) => boolean = () => false): Need[] {
  return needs.filter((n) => !quiet(n.workspace));
}

export function inboxGroups(needs: readonly Need[]): InboxGroup[] {
  return GROUPS.map((g) => ({ key: g.key, label: g.label, needs: needs.filter((n) => g.kinds.includes(n.kind)).slice().sort((a, b) => b.at - a.at) }))
    .filter((g) => g.needs.length > 0);
}

/** What the badge says: everything waiting, never the upcoming calls. */
export function inboxCount(needs: readonly Need[]): number {
  return needs.filter((n) => n.kind !== "upcoming").length;
}
