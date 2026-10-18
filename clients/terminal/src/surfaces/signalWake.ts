/** A TURN THE MEETING WOKE (ADR transcript signals): when the meeting's signals reach the brief's
 *  threshold, the company's flow wakes the agent in the meeting's chat with a composed prompt — the
 *  buffer of what was said. Nobody typed that prompt, so it is never the person's bubble; the reply
 *  is the agent's ordinary message, with a small "From the meeting" marker so the person knows why
 *  Vexa spoke up. Never a raw trigger or flow name.
 *
 *  Recognised from the record, the way every machine-composed turn is (`core/agent/shared/marks.py`):
 *  a flow mark `[vexa-flow:<flow>:<step>]` whose flow or step names a signal, or a turn whose history
 *  says `trigger: "signal"`. */

export const FROM_MEETING = "From the meeting";
/** What woke the reply, named by its SOURCE (founder 2026-10-16: a room's chat wake said "meeting"). */
export type WakeSource = "chat" | "call";
export function wakeLabel(t: { wokeBy?: WakeSource }): string { return t.wokeBy === "chat" ? "From the chat" : "From the call"; }
function sourceOf(t: { raw?: string; text?: string }): WakeSource {
  const m = FLOW_MARK.exec(t.raw ?? t.text ?? "");
  return m && /room|chat/.test(`${m[1]}:${m[2]}`) ? "chat" : "call";
}

const FLOW_MARK = /\[vexa-flow:([a-z0-9_-]{1,32}):([^\]]{0,512})\]/;

export function isSignalWake(t: { role: string; raw?: string; text?: string; trigger?: string }): boolean {
  if (t.role !== "user") return false;
  if (t.trigger === "signal") return true;
  const m = FLOW_MARK.exec(t.raw ?? t.text ?? "");
  return !!m && /signal/.test(`${m[1]}:${m[2]}`);
}

/** The history with every wake prompt dropped and the reply it produced marked `fromMeeting`. */
export function foldSignalWakes<T extends { role: string; raw?: string; text?: string; trigger?: string }>(turns: T[]): (T & { fromMeeting?: boolean; wokeBy?: WakeSource })[] {
  const out: (T & { fromMeeting?: boolean; wokeBy?: WakeSource })[] = [];
  let woke: WakeSource | null = null;
  for (const t of turns) {
    if (isSignalWake(t)) { woke = sourceOf(t); continue; }
    // the reply is whatever answered it — the history says "agent" (some records say "assistant")
    if (t.role !== "user" && woke) { out.push({ ...t, fromMeeting: true, wokeBy: woke }); woke = null; continue; }
    if (t.role === "user") woke = null;
    out.push(t);
  }
  return out;
}
