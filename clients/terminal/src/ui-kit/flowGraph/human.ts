/** A FLOW IN HUMAN TERMS (Vexa-ai/vexa#1783; founder, 2026-10-14, on a live call: "flows do not make
 *  sense to me either"). The diagram he was shown read `schedule 59 14 14 10 * → agent.run →
 *  background policy 3 allowed · 6 refused → this chat → bell`, with the next run "in 8759 h".
 *  Everything here says the same flow the way a person would: when it runs, what it does, where the
 *  results go, what it may do with nobody there (as a question), and why a run was refused (as a
 *  sentence with its fix). The engine's own view stays in `model.ts` / `FlowGraph`, behind "Details".
 *
 *  Pure: a flow definition and a clock in, words out. Times are on the viewer's clock. */
import { nextRun, type FlowDef } from "./model";

const pad = (n: number) => String(n).padStart(2, "0");
const hhmm = (d: Date) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const DAY = 86_400_000;
const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** What wakes an event-triggered routine, as something that happens. */
const EVENT_WORDS: Record<string, string> = {
  "meeting.completed": "a meeting ends", "meeting.started": "a meeting starts",
  "meeting.ended": "a meeting ends", "mail.received": "an email arrives",
  "calendar.event.upcoming": "a calendar meeting is about to start",
};

const isOnce = (def: FlowDef) => def.lifecycle === "once" || def.trigger?.kind === "at";
const paused = (def: FlowDef) => def.enabled === false || (!!def.status && def.status !== "active");

/** "today 23:00", "tomorrow 09:00", "Thu 09:00" within the week, else "Thu, 22 Oct 09:00". */
export function dateWords(d: Date, now: Date): string {
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(d) - day(now)) / DAY);
  const t = hhmm(d);
  if (diff === 0) return `today ${t}`;
  if (diff === 1) return `tomorrow ${t}`;
  if (diff === -1) return `yesterday ${t}`;
  if (diff > 1 && diff < 7) return `${d.toLocaleDateString([], { weekday: "short" })} ${t}`;
  const opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short",
    ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) };
  return `${d.toLocaleDateString([], opts)} ${t}`;
}

/** The one instant a one-time routine is for: the server's next run while it has not run, else the
 *  minute its trigger names (stored as a cron of that minute, in UTC). */
function onceAt(def: FlowDef, now: Date): Date | null {
  if (def.next_run && !def.last_run?.started_at) return new Date(def.next_run * 1000);
  const p = String(def.trigger?.cron || "").trim().split(/\s+/);
  if (p.length === 5 && p.slice(0, 4).every((x) => /^\d+$/.test(x))) {
    const [mi, ho, dom, mon] = p.map(Number);
    return new Date(Date.UTC(now.getUTCFullYear(), mon - 1, dom, ho, mi));
  }
  return nextRun(def.trigger?.cron, now);
}

function localHHMM(h: number, m: number): { at: string; shifts: boolean } {
  const now = new Date();
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h, m));
  return { at: hhmm(d), shifts: d.getDate() !== d.getUTCDate() };
}

function dayWords(dow: string): string | null {
  if (dow === "*") return "every day";
  if (dow === "1-5") return "every weekday";
  if (dow === "0,6" || dow === "6,0" || dow === "6-7" || dow === "6,7") return "every weekend day";
  if (/^[0-7](,[0-7])*$/.test(dow)) return `every ${dow.split(",").map((d) => WEEKDAY[Number(d) % 7]).join(", ")}`;
  return null;
}

/** A schedule in words, or null when this reader cannot say it (then: "on a schedule"). */
function scheduleWords(cron: string | null | undefined): string | null {
  const p = String(cron || "").trim().split(/\s+/);
  if (p.length !== 5) return null;
  const [mi, ho, dom, mon, dow] = p;
  const every = /^\*\/(\d+)$/;
  const num = /^\d+$/;
  if (every.test(mi) && ho === "*" && dom === "*" && mon === "*" && dow === "*") return `every ${mi.match(every)![1]} min`;
  if (num.test(mi) && ho === "*" && dom === "*" && mon === "*" && dow === "*") return `every hour at :${pad(+mi)}`;
  if (num.test(mi) && every.test(ho) && dom === "*" && mon === "*" && dow === "*") return `every ${ho.match(every)![1]} h at :${pad(+mi)}`;
  const span = /^(\d+)-(\d+)$/.exec(ho);
  if (every.test(mi) && span && dom === "*" && mon === "*") {
    const n = Number(mi.match(every)![1]);
    const last = 60 % n === 0 ? 60 - n : Math.floor(59 / n) * n;
    const days = dow === "*" ? "" : dow === "1-5" ? " on weekdays" : null;
    if (days === null) return null;
    return `every ${n} min, ${localHHMM(+span[1], 0).at}–${localHHMM(+span[2], last).at}${days}`;
  }
  if (num.test(mi) && num.test(ho) && dom === "*" && mon === "*") {
    const days = dayWords(dow);
    if (!days) return null;
    const { at, shifts } = localHHMM(+ho, +mi);
    // a day rule whose local time falls on another calendar day than in UTC would name the wrong
    // days, so that one alone stays in UTC (the same rule `cronText` keeps)
    return dow !== "*" && shifts ? `${days} ${pad(+ho)}:${pad(+mi)} UTC` : `${days} ${at}`;
  }
  return null;
}

/** WHEN: "every weekday 23:00", "once, today 15:59", "when a meeting ends". Never a cron string. */
export function whenWords(def: FlowDef, now: Date = new Date()): string {
  const t = def.trigger || {};
  if (t.kind === "event") {
    const e = String(t.event || "");
    return `when ${EVENT_WORDS[e] ?? (e ? e.replace(/[._]+/g, " ") : "something happens")}`;
  }
  if (isOnce(def)) {
    const d = onceAt(def, now);
    return d ? `once, ${dateWords(d, now)}` : "once";
  }
  return scheduleWords(t.cron) ?? "on a schedule";
}

/** The next run as a DATE ("today 23:00", "Thu 09:00"); minutes away only under the hour. Null when
 *  there is none: paused, event-driven, or a one-time routine that already ran. */
export function nextWords(def: FlowDef, now: Date = new Date()): string | null {
  if (paused(def) || def.trigger?.kind === "event") return null;
  let next: Date | null;
  if (isOnce(def)) {
    if (def.last_run?.started_at) return null;
    next = onceAt(def, now);
  } else {
    next = nextRun(def.trigger?.cron, now) ?? (def.next_run ? new Date(def.next_run * 1000) : null);
  }
  if (!next || next.getTime() <= now.getTime()) return null;
  const ms = next.getTime() - now.getTime();
  const words = dateWords(next, now);
  return ms < 60 * 60_000 ? `${words} (in ${Math.max(1, Math.round(ms / 60_000))} min)` : words;
}

// ── what it may do with nobody there ──────────────────────────────────────────────────────────
const GRANTS: Record<string, { label: string; words: string }> = {
  "bot.request": { label: "May send bots", words: "send bots to meetings" },
  "bot.stop": { label: "May stop its bots", words: "stop its bots" },
  "flow.retire_self": { label: "May retire itself", words: "retire itself when it is done" },
};

/** Which act a refusal was about, read off its words. */
function refusedAct(def: FlowDef): { act: string; grant?: string } {
  const r = def.last_refusal;
  const text = `${r?.text ?? ""} ${r?.reason ?? ""}`;
  if (/stop/i.test(text) && /bot/i.test(text)) return { act: "stop the bot", grant: "bot.stop" };
  if (/bot/i.test(text)) return { act: "send the bot", grant: "bot.request" };
  if (/retire/i.test(text)) return { act: "retire itself", grant: "flow.retire_self" };
  return { act: "finish its run" };
}

const granted = (def: FlowDef, g: string) => (def.grants ?? []).includes(g);

/** The sentence the person sends to give a routine one more permission — through the chat, where the
 *  agent records it on the routine (flows phase 0 grants are recorded only by a person). */
export const askFor = (def: FlowDef, grant: string) =>
  `Let my routine “${def.name}” ${GRANTS[grant]?.words ?? grant} when I'm not here.`;

export type PermissionRow = { grant: string; label: string; granted: boolean; ask: string };

/** "May send bots: no — allow?" — the permissions that matter for THIS routine: the ones it has, the
 *  ones its own steps or instruction need, and the one a refused run asked for. */
export function permissionRows(def: FlowDef): PermissionRow[] {
  const instr = def.instruction ?? "";
  const fix = def.last_refusal ? refusedAct(def).grant : undefined;
  const wants: Record<string, boolean> = {
    "bot.request": (def.steps ?? []).includes("request_meeting_bot") || /\bbots?\b/i.test(instr) || fix === "bot.request",
    "bot.stop": /\bstop\b[^.]*\bbots?\b|\bbots?\b[^.]*\bstop\b/i.test(instr) || fix === "bot.stop",
    // a one-time routine is retired by the scheduler after it fires (flows phase 0), so this is
    // asked about only when a run was refused for it, or it was given
    "flow.retire_self": fix === "flow.retire_self",
  };
  return Object.keys(GRANTS)
    .filter((g) => wants[g] || granted(def, g))
    .map((g) => ({ grant: g, label: GRANTS[g].label, granted: granted(def, g), ask: askFor(def, g) }));
}

/** A refused run as one sentence, and the permission that fixes it (absent when there is none to give
 *  or it was given since). */
export function refusalWords(def: FlowDef, now: Date = new Date()): { sentence: string; fix?: string } | null {
  const r = def.last_refusal;
  if (!r) return null;
  if (r.next_run_at) {
    void now;
    return { sentence: `It reached today's run limit; it can run again at ${hhmm(new Date(r.next_run_at * 1000))}.` };
  }
  const { act, grant } = refusedAct(def);
  const fix = grant && !granted(def, grant) ? grant : undefined;
  if (r.reason === "human_session_required" || /person|present|approv|nobody/i.test(r.text ?? "")) {
    return { sentence: `It couldn't ${act}: nobody was here to approve it.`, ...(fix ? { fix } : {}) };
  }
  // anything else: the server's own sentence, without the engine's names in it
  const clean = String(r.text || r.reason || "It was refused.")
    .replace(/\s*\([a-z0-9]+(?:_[a-z0-9]+)+\)/g, "")
    .replace(/\b[a-z0-9]+(?:_[a-z0-9]+)+\b/g, (w) => w.replace(/_/g, " "))
    .trim();
  return { sentence: clean, ...(fix ? { fix } : {}) };
}

/** How the last run went, in one word or two. */
export function statusWord(def: FlowDef, state?: "running" | "paused" | "retired"): string {
  if (state === "retired") return "retired";
  if (state === "paused" || paused(def)) return "paused";
  const r = def.last_refusal;
  const run = def.last_run;
  const refusalIsNews = r && !(r.at && run?.started_at && r.at < run.started_at && run.status === "done");
  if (refusalIsNews) {
    if (r.next_run_at) return "limit reached";
    const w = refusalWords(def);
    return w?.fix ? "needs permission" : "refused";
  }
  if (!run?.started_at && !run?.status) return "not run yet";
  if (run.status === "done") return "ok";
  if (run.status === "failed") return "failed";
  if (run.status && /refus/i.test(run.status)) return "refused";
  return "running";
}

/** THE STRIP'S ONE LINE: "Routine · every weekday 23:00 · next Thu 23:00 · ok". */
export function routineLine(def: FlowDef, now: Date = new Date(), state?: "running" | "paused" | "retired"): string {
  const next = state && state !== "running" ? null : nextWords(def, now);
  return ["Routine", whenWords(def, now), next ? `next ${next}` : null, statusWord(def, state)]
    .filter(Boolean).join(" · ");
}

const STEP_WORDS: Record<string, string> = {
  request_meeting_bot: "sends a bot to the meeting", deliver_message: "posts a message",
  post_event_card: "adds a card to this chat", agent_run: "asks the agent to do its task",
};

/** DOES: the instruction in the person's own words, else what its steps do. */
export function doesWords(def: FlowDef): string {
  const first = (def.instruction ?? "").split("\n").map((l) => l.trim()).find(Boolean);
  if (first) return first.length > 160 ? `${first.slice(0, 159).trimEnd()}…` : first;
  const words = (def.steps ?? []).map((s) => STEP_WORDS[s] ?? s.replace(/_/g, " "));
  return words.length ? words.join(", then ") : "asks the agent to do its task";
}

/** RESULTS GO TO: this chat, and the connection it notifies when it has one. */
export const resultsWords = (def: FlowDef) => (def.notify ? `this chat and ${def.notify}` : "this chat");
