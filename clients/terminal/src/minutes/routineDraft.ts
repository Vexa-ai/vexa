/** A DRAFT ROUTINE, WHERE ITS PERSON CAN REACH IT (live app.dev 2026-10-18).
 *
 *  An agent turn files a routine as a DRAFT (D24): nothing runs until the person turns it on from
 *  their own session. The agent told the founder it was waiting "on your Routines board" — a surface
 *  the shell2 sidebar does not have — and the draft showed nowhere he could reach: its chat carried
 *  no routine mark (that arrives with the first run), so the Routines filter skipped it, and the
 *  routine strip that could have resumed it sits behind "…" → Activity when rooms are on.
 *
 *  So a draft is drawn in two places, both one press from where the person is:
 *  - its HOME CHAT carries a line at the top — "Draft routine · Narrative field pulse · daily 08:00 ·
 *    Turn on" — outside the Activity menu;
 *  - the sidebar's ROUTINES filter lists it, marked Draft, with its schedule, its home chat and an
 *    on/off switch.
 *  Turning it on is `PATCH /api/routines/{name}/enabled` from the browser's session; agent-api
 *  refuses that to an agent turn and to an API key (D13). Once on, the card is no longer a draft:
 *  the chat is marked as the routine's (here at once, and on the server) and shows with the routine
 *  icon in the Routines filter like any other routine's chat.
 */
import { whenWords } from "../ui-kit/flowGraph/human";
import type { FlowDef } from "../ui-kit/flowGraph/model";

/** The routine card as `GET /api/routines` answers it — only the fields the draft surfaces read. */
export type RoutineCard = {
  name: string;
  status?: string;
  enabled?: boolean;
  draft?: boolean;
  cron?: string | null;
  trigger?: { kind?: string; cron?: string | null; event?: string } | null;
  thread?: string;
  flow?: { name?: string; scope?: string; version?: number } | null;
};

/** A draft: proposed by an agent and never turned on. A paused routine (it ran before) is not. */
export function isDraft(c: Pick<RoutineCard, "draft" | "status"> | undefined | null): boolean {
  return !!c && (c.draft === true || c.status === "draft");
}

/** A retired routine is history: listed nowhere as a routine. */
export function isRetired(c: Pick<RoutineCard, "status">): boolean {
  return /retired|deleted|done/i.test(c.status ?? "");
}

/** "narrative-field-pulse" → "Narrative field pulse": the flow name, as a person reads it. */
export function routineLabel(name: string): string {
  const words = String(name || "").replace(/[-_.]+/g, " ").replace(/\s+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Routine";
}

/** "daily 08:00" — the schedule on the viewer's clock, in the words the routine strip uses. */
export function draftWhen(c: RoutineCard, now: Date = new Date()): string {
  const trigger = c.trigger?.kind ? c.trigger : (c.cron ? { kind: "schedule", cron: c.cron } : null);
  // the founder's own words for the line (2026-10-18): "daily 08:00", not "every day 08:00"
  return whenWords({ name: c.name, trigger } as FlowDef, now).replace(/^every day\b/, "daily");
}

/** The words of the chat's draft line: "Draft routine · Narrative field pulse · daily 08:00". */
export function draftLine(c: RoutineCard, now: Date = new Date()): string {
  return `Draft routine · ${routineLabel(c.name)} · ${draftWhen(c, now)}`;
}

/** The drafts whose home chat is `session` — the ones its Turn on line is drawn for. */
export function draftsOfChat(cards: ReadonlyArray<RoutineCard> | null | undefined, session: string | undefined): RoutineCard[] {
  if (!cards || !session) return [];
  return cards.filter((c) => isDraft(c) && c.thread === session);
}

/** Every draft, for the sidebar's Routines filter. */
export function draftRoutines(cards: ReadonlyArray<RoutineCard> | null | undefined): RoutineCard[] {
  return (cards ?? []).filter((c) => isDraft(c) && !!c.name);
}
