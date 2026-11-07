/** A FLOW, DRAWN — the read-only model behind the routine strip's countdown and the flow graph
 *  (ADR-0046; founder, 2026-10-13: "a countdown till next execution, and a flow render … so they see
 *  what they are stacking"). Pure: a flow definition (and its last run, quota and background policy,
 *  when the server sent them) in; nodes, sentences and times out. No editor, nothing written. */

export type StepRun = { state?: string; at?: number; duration_s?: number };
export type FlowDef = {
  name: string;
  scope?: string;
  trigger?: { kind?: string; cron?: string | null; event?: string | null } | null;
  steps?: string[];
  status?: string;
  instruction?: string;
  thread?: string;
  notify?: string;
  kind?: string;
  wake?: string;
  filter?: Record<string, string>;
  enabled?: boolean;
  paused_reason?: string;
  last_run?: { id?: string; status?: string; reason?: string | null; started_at?: number; ended_at?: number;
               duration_s?: number; session?: string; steps?: Record<string, StepRun> } | null;
  last_refusal?: { reason?: string; text?: string; at?: number; next_run_at?: number | null } | null;
  runs_today?: number;
  runs_per_day?: number;
  policy?: { key: string; allowed: string[]; refused: string[] } | null;
  /** What the routine may do with nobody there, in the person's words (flows phase 0). */
  may?: string;
  /** A keyed schedule's next fires (ADR-0050): the bot before each meeting. */
  next_fires?: { key: string; fire_at: number; title?: string }[];
  /** The sensor feeding this chat, and its health. */
  sensor?: { name: string; kind: string; status: string; mode?: string; reason?: string | null;
             last_tick_at?: number | null; next_check_at?: number | null;
             last_events?: { type: string; title?: string; at: number }[] } | null;
  /** The same, as names (`bot.request`, `bot.stop`, `flow.retire_self`). */
  grants?: string[];
  /** "once" for a one-time routine (`trigger.kind: at`), else "recurring". */
  lifecycle?: string;
  /** The next run as the server computed it (epoch seconds), when it sent one. */
  next_run?: number | null;
};

export type NodeKind = "trigger" | "step" | "gate" | "output";
export type NodeStatus = "ok" | "refused" | "failed" | "running" | "never";
export type GraphNode = {
  id: string;
  kind: NodeKind;
  label: string;
  sub?: string;
  status?: NodeStatus;
  duration_s?: number;
  /** what a click peeks: the node's own configuration, as plain lines */
  detail: { title: string; lines: string[] };
};

// ── cron, stored in UTC, said on the viewer's clock ─────────────────────────────────────────
type Field = Set<number> | null;            // null = every value
const RANGES: [number, number][] = [[0, 59], [0, 23], [1, 31], [1, 12], [0, 6]];

function field(text: string, [lo, hi]: [number, number]): Field | undefined {
  if (text === "*") return null;
  const out = new Set<number>();
  for (const part of text.split(",")) {
    const [base, stepText] = part.split("/");
    const step = stepText === undefined ? 1 : Number(stepText);
    if (!Number.isInteger(step) || step < 1) return undefined;
    let a = lo, b = hi;
    if (base !== "*") {
      const [x, y] = base.split("-");
      a = Number(x); b = y === undefined ? (stepText === undefined ? a : hi) : Number(y);
      if (!Number.isInteger(a) || !Number.isInteger(b)) return undefined;
    }
    for (let v = a; v <= b; v += step) out.add(hi === 6 && v === 7 ? 0 : v);
  }
  for (const v of out) if (v < lo || v > (hi === 6 ? 7 : hi)) return undefined;
  return out;
}

export function parseCron(cron: string | null | undefined): Field[] | null {
  const parts = String(cron || "").trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const f = parts.map((p, i) => field(p, RANGES[i]));
  return f.some((x) => x === undefined) ? null : (f as Field[]);
}

const has = (f: Field, v: number) => f === null || f.has(v);

/** The next slot strictly after `now`, or null for a cron this reader cannot read. */
export function nextRun(cron: string | null | undefined, now: Date): Date | null {
  const f = parseCron(cron);
  if (!f) return null;
  const [mi, ho, dom, mon, dow] = f;
  const t = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(),
                              now.getUTCHours(), now.getUTCMinutes() + 1));
  for (let i = 0; i < 366 * 24 * 60; i++) {
    const dayOk = dom === null || dow === null
      ? has(dom, t.getUTCDate()) && has(dow, t.getUTCDay())
      : has(dom, t.getUTCDate()) || has(dow, t.getUTCDay());
    if (has(mon, t.getUTCMonth() + 1) && dayOk && has(ho, t.getUTCHours()) && has(mi, t.getUTCMinutes())) {
      return t;
    }
    t.setUTCMinutes(t.getUTCMinutes() + 1);
  }
  return null;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** The viewer's own clock: "07:00" for a UTC hour/minute today, and the place their time zone names. */
function localHHMM(h: number, m: number, now = new Date()): string {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h, m));
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}
export function zoneName(): string {
  try {
    const z = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    return z === "UTC" || z === "Etc/UTC" ? "UTC" : (z.split("/").pop() || z).replace(/_/g, " ");
  } catch { return "UTC"; }
}

/** "every 15 min", "every 30 min · 07:00–22:30 Lisbon", "hourly at :05", "daily at 10:00 London", else the cron. */
export function cronText(cron: string | null | undefined): string {
  const raw = String(cron || "").trim();
  const p = raw.split(/\s+/);
  if (p.length !== 5) return raw || "no schedule";
  const [mi, ho, dom, mon, dow] = p;
  const every = /^\*\/(\d+)$/;
  if (every.test(mi) && ho === "*" && dom === "*" && mon === "*" && dow === "*") return `every ${mi.match(every)![1]} min`;
  const span = /^(\d+)-(\d+)$/.exec(ho);
  if (every.test(mi) && span && dom === "*" && mon === "*") {
    const n = Number(mi.match(every)![1]);
    const last = 60 % n === 0 ? 60 - n : Math.floor(59 / n) * n;
    const days = dow === "*" ? "" : dow === "1-5" ? " · weekdays" : ` · days ${dow}`;
    return `every ${n} min · ${localHHMM(+span[1], 0)}–${localHHMM(+span[2], last)} ${zoneName()}${days}`;
  }
  if (/^\d+$/.test(mi) && every.test(ho) && dom === "*" && mon === "*" && dow === "*") return `every ${ho.match(every)![1]} h at :${pad(+mi)}`;
  if (/^\d+$/.test(mi) && ho === "*" && dom === "*" && mon === "*" && dow === "*") return `hourly at :${pad(+mi)}`;
  if (/^\d+$/.test(mi) && /^\d+$/.test(ho) && dom === "*" && mon === "*") {
    // the viewer's clock, like "Next run" beside it; a day rule whose local time falls on another
    // calendar day than in UTC would name the wrong days, so that one alone stays in UTC
    const now = new Date();
    const inst = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), +ho, +mi));
    const shifts = inst.getDate() !== inst.getUTCDate();
    const at = dow !== "*" && shifts ? `${pad(+ho)}:${pad(+mi)} UTC` : `${localHHMM(+ho, +mi, now)} ${zoneName()}`;
    if (dow === "*") return `daily at ${at}`;
    if (dow === "1-5") return `weekdays at ${at}`;
    return `at ${at} (days ${dow})`;
  }
  return raw;
}

/** "7:42", "1h 05m", "2d 3h" — a countdown that reads at a glance. */
export function countdown(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 3600) return `${Math.floor(s / 60)}:${pad(s % 60)}`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${pad(Math.floor((s % 3600) / 60))}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

/** An instant on the viewer's own clock ("19:42"), the same clock "Next run" and the schedule use. */
export const clock = (sec: number | null | undefined) =>
  sec ? new Date(sec * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : "";

/** What the strip says next to the routine's name, at `now`. */
export function scheduleLine(def: FlowDef, now: Date): string {
  if (def.enabled === false || (def.status && def.status !== "active")) return "Paused";
  const refusal = def.last_refusal;
  if (refusal?.next_run_at && refusal.next_run_at * 1000 > now.getTime()) {
    return `Limit reached — next possible ${clock(refusal.next_run_at)}`;
  }
  if (def.kind === "sink") {
    return `Cards from ${def.trigger?.event ?? "events"} · agent wakes ${def.wake && def.wake !== "never" ? def.wake : "never (a log)"}`;
  }
  if (def.trigger?.kind === "event") {
    const fired = def.last_run?.started_at ? ` · last fired ${clock(def.last_run.started_at)}` : "";
    return `Fires on ${def.trigger.event ?? "its event"}${fired}`;
  }
  const next = def.trigger?.kind === "event" ? null : nextRun(def.trigger?.cron, now);
  if (!next) return "No next run";
  const ms = next.getTime() - now.getTime();
  if (ms <= 60 * 60_000) return `Next run in ${countdown(ms)}`;
  const at = next.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const sameDay = next.toDateString() === now.toDateString();
  const tomorrow = new Date(now.getTime() + 86_400_000).toDateString() === next.toDateString();
  const day = sameDay ? "" : tomorrow ? " tomorrow" : ` ${next.toLocaleDateString([], { weekday: "short" })}`;
  const h = Math.floor(ms / 3_600_000), m = Math.floor((ms % 3_600_000) / 60_000);
  return `Next run ${at}${day} (in ${h} h ${m} min)`;
}

// ── the graph ─────────────────────────────────────────────────────────────────────────────────
const STEP_LABEL: Record<string, string> = { agent_run: "agent.run", deliver_message: "message.deliver",
                                            post_event_card: "event card", request_meeting_bot: "bot.request" };

function statusOf(state?: string): NodeStatus | undefined {
  if (!state) return undefined;
  if (/^(ok|done|confirmed|succeeded|applied|committed)$/i.test(state)) return "ok";
  if (/^reserved$/i.test(state)) return "running";
  if (/refus/i.test(state)) return "refused";
  if (/fail|error/i.test(state)) return "failed";
  return "running";
}

/** Left to right: the trigger, each step of the definition, the background-policy gate the steps
 *  pass, and where the result goes. */
export function flowGraph(def: FlowDef): GraphNode[] {
  const run = def.last_run || null;
  const nodes: GraphNode[] = [];
  const t = def.trigger || {};
  nodes.push(t.kind !== "event"
    ? { id: "trigger", kind: "trigger", label: "schedule", sub: cronText(t.cron),
        status: run ? "ok" : "never",
        detail: { title: "Trigger: schedule", lines: [`cron (UTC): ${t.cron ?? ""}`, cronText(t.cron)] } }
    : { id: "trigger", kind: "trigger", label: "event", sub: String(t.event || ""),
        detail: { title: "Trigger: event", lines: [String(t.event || "")] } });
  for (const step of def.steps ?? []) {
    const r = run?.steps?.[step];
    const lines = step === "agent_run" && def.instruction ? def.instruction.split("\n").slice(0, 12) : [step];
    nodes.push({ id: `step:${step}`, kind: "step", label: STEP_LABEL[step] ?? step.replace(/_/g, "."),
                 status: statusOf(r?.state) ?? (run ? statusOf(run.status) : "never"),
                 duration_s: r?.duration_s,
                 detail: { title: `Step: ${STEP_LABEL[step] ?? step}`, lines } });
  }
  if (def.policy) {
    const { key, allowed, refused } = def.policy;
    nodes.push({ id: "gate", kind: "gate", label: "background policy",
                 sub: allowed.length ? `${allowed.length} allowed · ${refused.length} refused` : "nothing allowed",
                 status: def.last_refusal ? "refused" : undefined,
                 detail: { title: `Policy gate: ${key}`,
                           lines: [`allowed with nobody present: ${allowed.join(", ") || "none"}`,
                                   `refused: ${refused.join(", ") || "none"}`] } });
  }
  nodes.push({ id: "out:chat", kind: "output", label: "this chat",
               sub: run?.session ? "every run lands here" : undefined,
               detail: { title: "Output: the routine's chat", lines: ["Every run is a turn in the chat the routine was made in.",
                                                                      def.thread ? `chat: ${def.thread}` : "its own chat"] } });
  if (def.notify) {
    nodes.push({ id: "out:notify", kind: "output", label: "notify", sub: def.notify,
                 detail: { title: "Output: outward notification", lines: [`connection: ${def.notify}`] } });
  }
  nodes.push({ id: "out:bell", kind: "output", label: "bell", sub: "when a run is refused",
               detail: { title: "Output: the bell", lines: ["A refused run is posted once a day to the chat and the waiting list."] } });
  return nodes;
}
