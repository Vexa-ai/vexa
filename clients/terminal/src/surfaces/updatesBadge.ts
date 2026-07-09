/** updatesBadge — a tiny cross-component signal for the "new updates" badge on the Knowledge nav.
 *
 *  The badge counts OTHER members' commits (kind === "member") across the caller's active workspaces
 *  that landed since the user last opened Knowledge. It must update even when the Knowledge list isn't
 *  mounted (the user is on Meetings/Sessions), so the poll lives in the always-mounted Workbench and
 *  publishes here; the nav subscribes. The "seen" watermark is a committer-timestamp persisted in
 *  localStorage so the badge survives reloads. */
let _count = 0;
const _subs = new Set<() => void>();

export const updatesBadge = {
  count: (): number => _count,
  set: (n: number): void => { if (n !== _count) { _count = n; _subs.forEach((f) => f()); } },
  subscribe: (f: () => void): (() => void) => { _subs.add(f); return () => { _subs.delete(f); }; },
};

const WATERMARK = "vexa.updates.seenTs";
export const updatesSeenTs = (): number => { try { return Number(localStorage.getItem(WATERMARK)) || 0; } catch { return 0; } };
/** Mark everything up to ``ts`` as seen (call when Knowledge is opened) → clears the badge. */
export const markUpdatesSeen = (ts: number): void => {
  try { if (ts > updatesSeenTs()) localStorage.setItem(WATERMARK, String(ts)); } catch { /* storage unavailable */ }
  updatesBadge.set(0);
};
