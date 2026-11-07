/** A CALL'S WORDS ON THE MEETINGS PAGE (founder validation O1, 2026-10-18), pure.
 *
 *  - named after who or where: "Call with Megan", "#pilot call" (the server writes the same names on
 *    the meeting row, `core/agent/control_plane/call_names.py`);
 *  - a live row: who is in now and for how long — "in now: Megan, you · 4:12";
 *  - an ended row: why and how long — "ended when everyone left · 12 min". */
import type { MeetingMock } from "../../surfaces/meetingModel";
import { meetingPhase } from "../../surfaces/meetingModel";

const first = (name: string) => String(name ?? "").trim().split(/\s+/)[0] ?? "";

export function personCallName(name: string): string {
  const n = String(name ?? "").trim();
  const f = n.includes("@") && !n.includes(" ") ? n.split("@")[0] : first(n);
  return f ? `Call with ${f}` : "Call";
}

export function roomCallName(title: string): string {
  const t = String(title ?? "").trim().replace(/^#+/, "").trim();
  return t ? `#${t} call` : "Room call";
}

/** "4:12" under an hour, "1 h 05 min" over it. */
export function elapsed(fromMs: number, toMs: number = Date.now()): string {
  const s = Math.max(0, Math.round((toMs - fromMs) / 1000));
  if (s < 3600) return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`;
}

/** Why a call ended, in words, from meeting-api's `completion_reason`. */
export function endedWhy(reason?: string): string {
  switch (reason) {
    case "left_alone": return "ended when everyone left";
    case "startup_alone": return "nobody joined";
    case "stopped": return "ended";
    case "max_duration": case "max_bot_time": return "reached the time limit";
    default: return "ended";
  }
}

const ms = (iso?: string) => { const t = iso ? Date.parse(iso) : NaN; return Number.isFinite(t) ? t : 0; };

/** "ended when everyone left · 12 min" (the minutes the call ran, when known). */
export function endedLine(m: Pick<MeetingMock, "completion_reason" | "start_time" | "end_time">): string {
  const a = ms(m.start_time), b = ms(m.end_time);
  const mins = a && b && b > a ? Math.max(1, Math.round((b - a) / 60_000)) : 0;
  return [endedWhy(m.completion_reason), mins ? `${mins} min` : ""].filter(Boolean).join(" · ");
}

/** Recent: Vexa calls that ended within the last `hours` — the place a call goes when the last person
 *  leaves (Live → Recent). Newest first. */
export function recentCalls(ms_: readonly MeetingMock[], now: number = Date.now(), hours = 6): MeetingMock[] {
  return ms_.filter((m) => m.platform === "vexa_call" && meetingPhase(m) === "post" && !m.artifacts_deleted
    && now - (ms(m.end_time) || ms(m.start_time) || ms(m.created_at)) <= hours * 3_600_000)
    .sort((a, b) => (ms(b.end_time) || ms(b.created_at)) - (ms(a.end_time) || ms(a.created_at)));
}

/** "in now: Megan, you" — the people in the call now, by first name, "you" for yourself. */
export function inNowLine(names: readonly string[], isMe: (n: string) => boolean): string {
  if (!names.length) return "nobody in now";
  const words = names.map((n) => (isMe(n) ? "you" : first(n) || n));
  return `in now: ${words.slice(0, 3).join(", ")}${words.length > 3 ? ` +${words.length - 3}` : ""}`;
}

/** A ROW'S NAME ON THE MEETINGS PAGE (founder, live app.dev 2026-10-18: a column of identical "Call with
 *  Vexa" rows). The one name rule (`callLabel`) stays; a Vexa call it can only call "Call with Vexa" —
 *  nobody named, no person, no room, typically a past one from before calls were named — is told apart
 *  by its day and time: "Call with Vexa · Thu 15:30". */
export function rowLabel(m: MeetingMock, label: string, locale?: string): string {
  if (m.platform !== "vexa_call" || label !== "Call with Vexa") return label;
  const t = ms(m.start_time) || ms(m.created_at);
  if (!t) return label;
  const d = new Date(t);
  const day = d.toLocaleDateString(locale ?? [], { weekday: "short" });
  const time = d.toLocaleTimeString(locale ?? [], { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${label} · ${day} ${time}`;
}
