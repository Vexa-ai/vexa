"use client";
/** A ROUTINE'S THREAD, AND ITS CONTROLS (ADR-0046 decision 12).
 *
 *  Every run of one of the person's routines lands in this thread; they can read what each run did
 *  and reply. From here they pause, resume or retire the routine — through the routine routes,
 *  which act on THEIR flow only (flows checks ownership). Retiring keeps the thread: it is history.
 *  The strip counts down to the next run, says the last run and today's count, and expands the
 *  routine's flow graph. Neutral throughout: Retire is reversible and asks first.
 */
import { useEffect, useState } from "react";
import type { RoutineRef } from "./chats";
import { FlowGraph } from "../ui-kit/flowGraph/FlowGraph";
import { clock, cronText, scheduleLine, type FlowDef } from "../ui-kit/flowGraph/model";
import { askFor, doesWords, nextWords, permissionRows, refusalWords, resultsWords, routineLine, whenWords } from "../ui-kit/flowGraph/human";
import { ChevronDown } from "lucide-react";
import { ASK_CHAT_EVENT } from "../platform/events";
import { useChatActive } from "../surfaces/chatActivity";
import { draftLine, draftsOfChat, isDraft, type RoutineCard } from "./routineDraft";
import "./minutes.css";

/** The three verbs, as the routine adapter routes spell them. Exported for the delete path and the
 *  tests; every call answers `true` when the routine took the change. */
/** Said when a routine was paused, resumed or retired here, so the rail re-reads the routines. */
export const ROUTINES_CHANGED_EVENT = "vexa:routines-changed";
const changed = () => { if (typeof window !== "undefined") window.dispatchEvent(new Event(ROUTINES_CHANGED_EVENT)); };

export async function setRoutineRunning(flow: string, running: boolean): Promise<boolean> {
  const r = await fetch(`/api/routines/${encodeURIComponent(flow)}/enabled`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: running }),
  });
  if (r.ok) changed();
  return r.ok;
}

export async function retireRoutine(flow: string): Promise<boolean> {
  const r = await fetch(`/api/routines/${encodeURIComponent(`flow:${flow}`)}`, { method: "DELETE" });
  if (r.ok) changed();
  return r.ok;
}

/** The routine the OPEN chat belongs to, or undefined. The strip is drawn for that chat only — a
 *  routine's own thread — so any other chat, a new empty one, or none never carries it. */
export function routineOfOpenChat(
  chats: ReadonlyArray<{ id: string; routine?: RoutineRef }>, openId: string | undefined,
): RoutineRef | undefined {
  if (!openId) return undefined;
  return chats.find((c) => c.id === openId)?.routine;
}

/** WHY THIS ROUTINE DID NOT RUN, or why it is paused — the sentence flows or agent-api wrote, read off
 *  the routine's card; null when it is running normally. */
export function routineNotice(card: { last_refusal?: { text?: string } | null; paused_reason?: string;
                                      enabled?: boolean } | undefined): string | null {
  if (!card) return null;
  if (card.enabled === false && card.paused_reason) return `Paused — ${card.paused_reason}`;
  return card.last_refusal?.text || null;
}

/** The routine card (`GET /api/routines`) as the flow graph's definition. */
export function flowDefOf(card: Record<string, unknown> | undefined, name: string): FlowDef {
  const c = (card ?? {}) as Record<string, any>;  // eslint-disable-line @typescript-eslint/no-explicit-any
  return {
    name, scope: c.flow?.scope,
    trigger: c.trigger?.kind ? c.trigger : (c.cron ? { kind: "schedule", cron: c.cron } : null),
    steps: Array.isArray(c.steps) && c.steps.length ? c.steps : ["agent_run"], status: c.status,
    instruction: c.plan_summary, thread: c.thread, notify: c.notify, enabled: c.enabled,
    kind: c.kind, wake: c.wake, filter: c.filter,
    paused_reason: c.paused_reason, last_run: c.last_run, last_refusal: c.last_refusal,
    runs_today: c.runs_today, runs_per_day: c.runs_per_day, policy: c.policy, may: c.may,
    next_fires: c.next_fires, sensor: c.sensor,
    grants: Array.isArray(c.grants) ? c.grants : undefined, lifecycle: c.lifecycle, next_run: c.next_run,
  };
}

/** WHAT THIS CHAT OWNS, in words: one line per routine — what wakes it and where its output goes. */
export function ownershipLine(def: FlowDef): string {
  const t = def.trigger;
  const wakes = t?.kind === "event" ? `wakes on ${t.event}` : `wakes ${cronText(t?.cron)}`;
  const out = def.notify ? `output: this chat and ${def.notify}` : "output: this chat";
  if (def.kind === "sink") {
    const f = Object.entries(def.filter ?? {}).map(([k, v]) => `${k} ~ ${v}`).join(", ");
    return `⇢ sink ${def.name} — ${t?.event ?? "events"}${f ? ` where ${f}` : ""} land here as cards · agent wakes ${def.wake && def.wake !== "never" ? def.wake : "never"}`;
  }
  return `${def.trigger?.kind === "event" ? "⇢ event agent" : "⏱ routine"} ${def.name} — ${wakes} · ${out}`;
}

/** "last run 14:30 UTC · ok · 41s" and "12/48 runs today", from the card. */
export function runFacts(def: FlowDef): string[] {
  const out: string[] = [];
  const r = def.last_run;
  if (r?.started_at) {
    const status = r.status === "done" ? "ok" : r.status === "failed" ? "failed" : r.status || "";
    out.push(`last run ${clock(r.started_at)}${status ? ` · ${status}` : ""}${r.duration_s ? ` · ${r.duration_s}s` : ""}`);
  } else {
    out.push("not run yet");
  }
  if (def.runs_per_day) out.push(`${def.runs_today ?? 0}/${def.runs_per_day} runs today`);
  return out;
}

/** "Sensor video-meetings: active · checked 12:01 · next check 12:02" — or why it is not. */
export function sensorLine(def: FlowDef): string | null {
  const s = def.sensor;
  if (!s) return null;
  const parts = [`Sensor ${s.name}: ${s.status}${s.reason ? ` — ${s.reason}` : ""}`];
  if (s.last_tick_at) parts.push(`checked ${clock(s.last_tick_at)}`);
  if (s.next_check_at && (s.status === "active" || s.status === "degraded")) parts.push(`next check ${clock(s.next_check_at)}`);
  const last = s.last_events?.[s.last_events.length - 1];
  if (last) parts.push(`last: ${last.title || last.type}`);
  return parts.join(" · ");
}

/** "Next: 10:59 Sync · 14:29 Review" — a keyed schedule's next fires. */
export function firesLine(def: FlowDef): string | null {
  const f = def.next_fires;
  if (!f) return null;
  if (!f.length) return "Nothing armed yet";
  return "Next: " + f.slice(0, 3).map((x) => `${clock(x.fire_at)}${x.title ? ` ${x.title}` : ""}`).join(" · ");
}

/** THE STRIP (founder, 2026-10-14: "total mess here"). ONE LINE by default — "Routine · every weekday
 *  23:00 · next Thu 23:00 · ok" — and a small expand. Expanded, the flow in human terms (`human.ts`):
 *  When → Does → Results go to, what it may do with nobody there as a question with Allow, a refused
 *  run as a sentence with its fix, and Pause / Retire. The engine's view — the graph, the raw facts,
 *  the may-sentence — waits behind "Details" for operators. Allow does not grant anything here: it
 *  asks in the routine's chat, where the agent records the permission (only a person can). */
export function RoutineBar({ routine, session }: { routine: RoutineRef; session?: string }) {
  const [state, setState] = useState<"running" | "paused" | "retired">("running");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [card, setCard] = useState<Record<string, unknown> | undefined>(undefined);
  const [now, setNow] = useState(() => new Date());
  const [open, setOpen] = useState(false);
  const [details, setDetails] = useState(false);
  const [confirmRetire, setConfirmRetire] = useState(false);
  useEffect(() => {
    let live = true;
    void fetch("/api/routines").then((r) => (r.ok ? r.json() : null)).then((d) => {
      if (!live || !d) return;
      const found = (d.routines ?? []).find((c: { name?: string }) => c.name === routine.flow);
      setCard(found);
      setNotice(routineNotice(found));
      if (found && found.enabled === false) setState("paused");
    }).catch(() => { /* the strip still works without the reason */ });
    return () => { live = false; };
  }, [routine.flow]);
  useEffect(() => {
    // the line names a date, so it re-reads once a minute; under the hour it says minutes
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);
  const act = async (fn: () => Promise<boolean>, next: typeof state) => {
    setError(null);
    try {
      if (await fn()) setState(next);
      else setError("That did not go through — try again.");
    } catch {
      setError("That did not go through — try again.");
    }
  };
  const def = { ...flowDefOf(card, routine.flow), ...(state !== "running" ? { enabled: false } : {}) };
  const thread = session || def.thread;
  const ask = (prompt: string) => {
    if (!thread) return;
    window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { session: thread, prompt, display: prompt } }));
  };
  const line = card ? routineLine(def, now, state) : `Routine · ${routine.flow}`;
  const perms = card ? permissionRows(def) : [];
  const refusal = card && state !== "retired" ? refusalWords(def, now) : null;
  const whyPaused = state === "paused" && def.paused_reason ? `Paused — ${def.paused_reason}` : null;
  return (
    <div data-routine-bar={routine.flow} className="vx-routine-bar vx-routine-col" role="region" aria-label="Routine">
      <button type="button" data-routine-expand className="vx-routine-line" aria-expanded={open}
        title={open ? "Hide what this routine does" : "What this routine does"} onClick={() => setOpen((v) => !v)}>
        <span data-routine-line className="vx-routine-line-text">{line}</span>
        <ChevronDown size={14} strokeWidth={1.75} aria-hidden className="vx-routine-chev" data-open={open ? "" : undefined} />
      </button>
      {error && <span role="alert" className="vx-routine-text">{error}</span>}
      {open && (
        <div data-routine-flow className="vx-routine-flow">
          <dl className="vx-routine-steps">
            <div><dt>When:</dt> <dd>{whenWords(def, now)}{(() => { const n = state === "running" ? nextWords(def, now) : null; return n ? ` — next ${n}` : ""; })()}</dd></div>
            <div><dt>Does:</dt> <dd>{doesWords(def)}</dd></div>
            <div><dt>Results go to:</dt> <dd>{resultsWords(def)}</dd></div>
          </dl>
          {sensorLine(def) && <p data-routine-sensor className="vx-routine-text">{sensorLine(def)}</p>}
          {firesLine(def) && <p data-routine-fires className="vx-routine-text">{firesLine(def)}</p>}
          {perms.map((p) => (
            <p key={p.grant} data-routine-perm={p.grant} className="vx-routine-text">
              {p.label}: {p.granted ? "yes" : <>no — <button type="button" className="vx-routine-link" onClick={() => ask(p.ask)}>allow?</button></>}
            </p>
          ))}
          {(refusal || whyPaused) && (
            <p role="status" data-routine-notice className="vx-routine-text">
              {whyPaused ?? refusal!.sentence}
              {/* its fix is the permission row above ("May send bots: no — allow?"), never a second button;
                  a fix with no row (an unexpected grant) still gets its own */}
              {!whyPaused && refusal?.fix && !perms.some((x) => x.grant === refusal.fix) && <> <button type="button" className="vx-routine-link"
                onClick={() => ask(askFor(def, refusal.fix!))}>Allow it</button></>}
            </p>
          )}
          <div className="vx-routine-actions">
            {state === "running" && (
              <button className="vx-routine-btn" onClick={() => act(() => setRoutineRunning(routine.flow, false), "paused")}>Pause</button>)}
            {state === "paused" && (
              <button className="vx-routine-btn" onClick={() => act(() => setRoutineRunning(routine.flow, true), "running")}>Resume</button>)}
            {state !== "retired" && !confirmRetire && (
              <button className="vx-routine-btn" onClick={() => setConfirmRetire(true)}>Retire</button>)}
            {state !== "retired" && confirmRetire && (
              <span className="vx-routine-confirm" role="group" aria-label="Retire this routine?">
                <span className="vx-routine-text">Retire it? It stops running; this chat stays.</span>
                <button className="vx-routine-btn" onClick={() => { setConfirmRetire(false); void act(() => retireRoutine(routine.flow), "retired"); }}>Yes, retire</button>
                <button className="vx-routine-btn" onClick={() => setConfirmRetire(false)}>Keep</button>
              </span>)}
            <button type="button" data-routine-details className="vx-routine-link vx-routine-details" aria-expanded={details}
              onClick={() => setDetails((v) => !v)}>Details</button>
          </div>
          {details && (
            <div data-routine-raw className="vx-routine-raw">
              <div className="vx-routine-text">{card ? ownershipLine(def) : routine.flow}</div>
              <div data-routine-facts className="vx-routine-facts">
                <span data-routine-next className="vx-routine-num">{state === "retired" ? "Retired" : scheduleLine(def, now)}</span>
                {runFacts(def).map((f) => <span key={f}>{f}</span>)}
                {def.may && <span data-routine-may className="vx-routine-text">{def.may}</span>}
                {notice && <span className="vx-routine-text">{notice}</span>}
              </div>
              <FlowGraph flow={def} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** EVERY ROUTINE THIS CHAT OWNS (a chat is a standing concern that may own several — production
 *  monitoring wakes on each). One strip per routine whose chat is `session`; the rail's marker
 *  (`fallback`) still draws its strip before the routine list answers. A DRAFT is not one of them:
 *  it has its own line (`ChatRoutineDrafts`) until the person turns it on. */
export function routinesOfChat(cards: ReadonlyArray<{ name?: string; thread?: string; draft?: boolean; status?: string }>, session: string): string[] {
  return cards.filter((c) => c.thread === session && c.name && !isDraft(c)).map((c) => String(c.name));
}

export function ChatRoutines({ session, fallback, stage }: { session: string; fallback?: RoutineRef; stage?: string }) {
  const [names, setNames] = useState<string[] | null>(null);
  const [drafts, setDrafts] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    const read = () => void fetch("/api/routines").then((r) => (r.ok ? r.json() : null)).then((d) => {
      if (!live || !d) return;
      setNames(routinesOfChat(d.routines ?? [], session));
      setDrafts(draftsOfChat(d.routines ?? [], session).map((c) => c.name));
    }).catch(() => { /* the marker's strip still draws */ });
    read();
    // a draft turned on here becomes a routine of this chat: its strip appears without a reload
    window.addEventListener(ROUTINES_CHANGED_EVENT, read);
    return () => { live = false; window.removeEventListener(ROUTINES_CHANGED_EVENT, read); };
  }, [session]);
  const list = names ?? [];
  const all = fallback && !list.includes(fallback.flow) && !drafts.includes(fallback.flow) ? [fallback.flow, ...list] : list;
  // one quiet line, like the routine's: what this chat is and where it stands
  const stageLine = stage ? <div data-concern-stage={stage} className="vx-routine-bar vx-routine-text"
    title="A concern moves opened → active → waiting → resolved → archived.">Concern · {stage}</div> : null;
  if (!all.length) return stageLine;
  return <>{stageLine}{all.map((n) => <RoutineBar key={`${session}:${n}`} session={session} routine={{ flow: n, scope: fallback?.scope ?? "", version: fallback?.version ?? 0 }} />)}</>;
}

/** ONE DRAFT'S LINE at the top of its home chat: "Draft routine · Narrative field pulse · daily 08:00
 *  · Turn on". The press is the person's own, from this browser's session — agent-api refuses it to
 *  an agent turn and to an API key — and it is the only way the draft starts. */
export function DraftRoutineLine({ card, now }: { card: RoutineCard; now?: Date }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const turnOn = async () => {
    setBusy(true); setError(null);
    try {
      if (!(await setRoutineRunning(card.name, true))) setError("It did not turn on — try again.");
    } catch {
      setError("It did not turn on — try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div data-routine-draft={card.name} className="vx-routine-bar vx-routine-draft" role="region" aria-label="Draft routine">
      <span data-routine-draft-line className="vx-routine-line-text">{draftLine(card, now)}</span>
      <button type="button" data-routine-turn-on className="vx-routine-btn" disabled={busy}
        title="Turn this routine on — it runs on its schedule in this chat" onClick={() => void turnOn()}>Turn on</button>
      {error && <span role="alert" className="vx-routine-text">{error}</span>}
    </div>
  );
}

/** THE DRAFTS WHOSE HOME IS THIS CHAT, from the routine cards the shell already reads. Drawn above
 *  the conversation in every layout — never behind "…" → Activity — because a draft does nothing
 *  until its person sees it. When the chat's turn ends (the agent may just have filed one) the
 *  cards are read again. */
export function ChatRoutineDrafts({ session, cards }: { session: string; cards: ReadonlyArray<RoutineCard> | null }) {
  const active = useChatActive(session || null);
  const [was, setWas] = useState(active);
  useEffect(() => {
    if (was && !active && typeof window !== "undefined") window.dispatchEvent(new Event(ROUTINES_CHANGED_EVENT));
    setWas(active);
  }, [active, was]);
  const drafts = draftsOfChat(cards, session);
  if (!drafts.length) return null;
  return <>{drafts.map((c) => <DraftRoutineLine key={`${session}:${c.name}`} card={c} />)}</>;
}
