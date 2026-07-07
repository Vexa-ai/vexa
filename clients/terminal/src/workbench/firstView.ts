/** firstView — the pure priority function for the landing/first-view resolver (Workbench applies it).
 *
 *  On landing we pick ONE arrangement by what's SHARED with the user, instead of letting several surfaces
 *  self-open and race. Kept pure (no layout/DOM) so the priority is unit-tested in isolation; Workbench's
 *  resolveFirstView gathers the inputs (localStorage tshare id, the active-set shared mount, the live-
 *  meeting cache, whether the dock restored empty) and executes the returned plan. */

export interface FirstViewInputs {
  /** an explicit shared meeting from a ?tshare= link (InviteRedeemer stashed it before the reload) */
  sharedMeetingId: string | null;
  /** a shared workspace connected to the user (a non-primary 'shared' mount in the active set) */
  sharedSlug: string | null;
  /** a meeting currently live (best-effort — the cache may be cold on a brand-new landing) */
  liveMeetingId: string | null;
  /** the dock restored NO tabs — a genuine first landing (vs a returning user with a saved layout) */
  fresh: boolean;
}

export type FirstViewPlan =
  | { kind: "meeting-and-workspace"; meetingId: string; slug: string }  // README is the canvas, meeting present (its live badge shows)
  | { kind: "meeting"; meetingId: string }                              // the meeting is the view
  | { kind: "workspace-readme"; slug: string }                         // the shared workspace's README, pinned
  | { kind: "live-meeting"; meetingId: string }                       // a live meeting already known, no shares
  | { kind: "own-readme" }                                            // the user's own README-onboarding
  | { kind: "noop" };                                                 // returning user, nothing shared — leave their layout

/** Decide the single landing arrangement. Explicit shared meetings win and apply even to a returning user
 *  (they clicked a share link); the default README/live-meeting arms only assert on a fresh dock so a
 *  returning user's saved layout is never disturbed. */
export function firstViewPlan(i: FirstViewInputs): FirstViewPlan {
  if (i.sharedMeetingId && i.sharedSlug) return { kind: "meeting-and-workspace", meetingId: i.sharedMeetingId, slug: i.sharedSlug };
  if (i.sharedMeetingId) return { kind: "meeting", meetingId: i.sharedMeetingId };
  if (!i.fresh) return { kind: "noop" };
  if (i.sharedSlug) return { kind: "workspace-readme", slug: i.sharedSlug };
  if (i.liveMeetingId) return { kind: "live-meeting", meetingId: i.liveMeetingId };
  return { kind: "own-readme" };
}
