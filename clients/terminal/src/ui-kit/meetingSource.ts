/** meetingSource — the meetings list a rendered document reads, supplied from above.
 *
 *  A meeting named in a reply or a page renders as a chip with the meeting's title and date
 *  (`MeetingChip`), answered from the reader's meetings list. That list is the surfaces' store
 *  (`surfaces/liveMeetings`), which sits above the ui-kit: importing it from here made ui-kit and
 *  surfaces import each other (Vexa-ai/vexa#1783). So the ui-kit names the shape it reads and the
 *  store supplies itself (`provideMeetingSource`, called where the store is defined). Nothing
 *  supplied means an empty, loaded list — a meeting link renders as plain text, never a guess.
 */

/** The fields of a meetings-list row the chip reads. */
export interface MeetingRow {
  id: string;
  platform: string;
  native_id?: string;
  title_custom?: string;
  start_time?: string;
  scheduled_at?: string;
}

export interface MeetingSource {
  /** The reader's meetings list (a hook: called during render). */
  useMeetings(): readonly MeetingRow[];
  /** Whether the list has loaded at least once (a hook). */
  useLoaded(): boolean;
  /** Ask once for a row that is not in the list yet. */
  ensureKnown(ref: string): void;
}

const EMPTY: readonly MeetingRow[] = [];
const NONE: MeetingSource = { useMeetings: () => EMPTY, useLoaded: () => true, ensureKnown: () => {} };
let source: MeetingSource = NONE;

/** Supply the meetings list. Called once by the store that owns it; tests supply their own. */
export function provideMeetingSource(s: MeetingSource | null): void {
  source = s ?? NONE;
}

export const useSourceMeetings = (): readonly MeetingRow[] => source.useMeetings();
export const useSourceMeetingsLoaded = (): boolean => source.useLoaded();
export const ensureSourceMeetingKnown = (ref: string): void => source.ensureKnown(ref);
