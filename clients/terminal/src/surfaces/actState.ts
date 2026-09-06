"use client";
/** WHAT THE CONTROL THAT WAS PRESSED IS DOING (Vexa-ai/vexa#1604).
 *
 *  Founder, 2026-09-06, having pressed "Create this page" on an empty page: the control stays
 *  exactly as it was while the job runs in the background, and the only sign of life is a row in
 *  the chat — *"this thing should indicate it's actually working"*.
 *
 *  So an act's state lives HERE, keyed by the one thing a press and a job already agree on: the
 *  act's TARGET (`jobs.actTarget`, the client's spelling of `chat_intents.job_target`). The control
 *  reads its own target back and says what is happening to it, where it was pressed; the chat's job
 *  row goes on saying the same thing at the foot of the transcript. Two readings of one fact, never
 *  two facts.
 *
 *  ONE RAISER, ONE LOWERER — the rule this workspace applies to every surface with two participants.
 *  The PRESS raises the record (`minutes/extend.postIntent`, the single door every act goes through)
 *  and the JOB LIFECYCLE lowers it (`surfaces/chat.tsx`, the only reader of `job-started` /
 *  `job-done` / `job-failed` there is). Nothing else writes here: a control never stops its own
 *  spinner on a timer, because a spinner that stops before its job does is exactly the lie this
 *  file exists to stop telling.
 *
 *  The transitions are pure functions over one map — like `jobs.ts` beside it, and for the same
 *  reason: what goes quietly wrong is the bookkeeping, not the React. The store underneath them is
 *  a thin skin.
 */
import { useCallback, useSyncExternalStore } from "react";
import { QUEUED_LINE, stepsPhrase } from "./jobs";

/** Where an act is. `queued` and `working` are alive; `failed` is a record that OUTLIVES its job on
 *  purpose — it is the one line the control has to show, and the act it offers again. A landed act
 *  has no phase at all: the record goes, and the page it wrote is the result. */
export type ActPhase = "queued" | "working" | "failed";

export interface ActRec {
  /** the act, for the word the control says while it runs — `create` / `extend` / `extend_transcript` */
  kind: string;
  phase: ActPhase;
  /** the server's job id, once there is one. How a step and an ending find the control that is
   *  watching: the press knows only a target, and the job's own events know only an id. */
  job?: string;
  steps: number;
  /** the last step's short label — the same tail the chat's job row shows */
  label: string;
  /** what went wrong. `failed` only. */
  line?: string;
}

/** Every live act, by target. Small by construction: one entry per act in flight. */
export type Acts = Readonly<Record<string, ActRec>>;

const without = (acts: Acts, target: string): Acts => {
  if (!(target in acts)) return acts;
  const next = { ...acts };
  delete next[target];
  return next;
};

/** Find the record a job id belongs to. Linear over a handful of entries, and no second map to keep
 *  in step with this one — a job id that has drifted out of its record is a class of bug that only
 *  exists once there are two places to write it. */
const byJob = (acts: Acts, job: string): string | null =>
  job ? (Object.keys(acts).find((t) => acts[t].job === job) ?? null) : null;

/** THE PRESS. Working from the instant it is pressed — before the wire, before the turn, before the
 *  job has an id — because the founder's complaint is about the moment of the press, and every
 *  later moment already had a row somewhere. A record standing on a failed act is replaced: this
 *  press IS the retry. */
export function pressAct(acts: Acts, a: { target: string; kind: string }): Acts {
  if (!a.target) return acts;
  return { ...acts, [a.target]: { kind: a.kind, phase: "working", steps: 0, label: "" } };
}

/** PRESSED, BUT THE CHAT IS MID-TURN (Vexa-ai/vexa#1594) — the act fires next, and the control says
 *  so in place, in the same words the chat's queued row uses. */
export function queueAct(acts: Acts, a: { target: string; kind: string }): Acts {
  if (!a.target) return acts;
  const rec = acts[a.target];
  return { ...acts, [a.target]: { ...(rec ?? { kind: a.kind, steps: 0, label: "" }), kind: a.kind, phase: "queued", steps: 0, label: "" } };
}

/** THE QUEUED ACT IS GOING OUT NOW. The turn in front of it ended and this one is on the wire, so
 *  "queued behind the current turn" has stopped being true — and a control still saying it is the
 *  same defect as one still saying nothing, a minute later. Only a queued record moves: a running
 *  act is already working and must not have its step count reset by the send that started it. */
export function sendAct(acts: Acts, target: string): Acts {
  const rec = acts[target];
  if (!rec || rec.phase !== "queued") return acts;
  return { ...acts, [target]: { ...rec, phase: "working", steps: 0, label: "" } };
}

/** THE JOB NAMES ITSELF. The press's record learns the id it will be stepped and ended by — one
 *  record from the press to the landing, rather than one stopping and another starting. */
export function startAct(acts: Acts, j: { job: string; kind: string; target: string }): Acts {
  if (!j.target) return acts;
  return { ...acts, [j.target]: { kind: j.kind, phase: "working", job: j.job, steps: 0, label: "" } };
}

export function stepAct(acts: Acts, job: string, label: string): Acts {
  const target = byJob(acts, job);
  if (!target) return acts;
  const rec = acts[target];
  return { ...acts, [target]: { ...rec, phase: "working", steps: rec.steps + 1, label } };
}

/** LANDED OR DIED. Landed → the record goes and the control is a control again; the page it wrote
 *  is the result, and the commit event has already put it on screen. Died → the record STAYS,
 *  carrying the one line the control shows and the act it offers again. */
export function endAct(acts: Acts, job: string, ok: boolean, line: string): Acts {
  const target = byJob(acts, job);
  if (!target) return acts;
  if (ok) return without(acts, target);
  return { ...acts, [target]: { ...acts[target], phase: "failed", line } };
}

/** THE TURN ENDED AND NO JOB EVER CLAIMED THIS ACT — a refusal, an error, or a deployment whose
 *  worker still runs the act inline. Whatever happened, nothing is running, so nothing may spin. A
 *  failure is left alone: it is not waiting for anything, it is telling the person something. */
export function settleAct(acts: Acts, target: string): Acts {
  const rec = acts[target];
  if (!rec || rec.phase === "failed") return acts;
  return without(acts, target);
}

/** Drop the record outright — the control asking for the act again after a failure. */
export const clearAct = (acts: Acts, target: string): Acts => without(acts, target);

// ── the store ────────────────────────────────────────────────────────────────────────────────────

let acts: Acts = {};
const listeners = new Set<() => void>();

const commit = (next: Acts): void => {
  if (next === acts) return;
  acts = next;
  for (const l of listeners) l();
};

export const actsNow = (): Acts => acts;

export const actPressed = (a: { target: string; kind: string }): void => commit(pressAct(acts, a));
export const actQueued = (a: { target: string; kind: string }): void => commit(queueAct(acts, a));
export const actSending = (target: string): void => commit(sendAct(acts, target));
export const actStarted = (j: { job: string; kind: string; target: string }): void => commit(startAct(acts, j));
export const actStepped = (job: string, label: string): void => commit(stepAct(acts, job, label));
export const actEnded = (job: string, ok: boolean, line: string): void => commit(endAct(acts, job, ok, line));
export const actSettled = (target: string): void => commit(settleAct(acts, target));
export const actCleared = (target: string): void => commit(clearAct(acts, target));

/** For tests: module state outlives a `cleanup()`, and one suite's act must not run in the next. */
export const resetActs = (): void => { acts = {}; for (const l of listeners) l(); };

/** The state of ONE act, for the control that fired it. `null` target = this control has fired
 *  nothing, which is the ordinary case and must not subscribe to anything meaningful. */
export function useActState(target: string | null): ActRec | null {
  const subscribe = useCallback((cb: () => void) => {
    listeners.add(cb);
    return () => { listeners.delete(cb); };
  }, []);
  const read = useCallback(() => (target ? acts[target] ?? null : null), [target]);
  return useSyncExternalStore(subscribe, read, read);
}

// ── what it says ─────────────────────────────────────────────────────────────────────────────────

/** THE WORD, WHILE IT RUNS. The founder named both of them — *"Creating…" / "Extending…"* — and they
 *  are the act's own verb, not a generic "Working": a person who pressed Create is owed the word
 *  they pressed. Extend on a transcript passage is Extend to the person who pressed it
 *  (Vexa-ai/vexa#1596), so it says the same word its button did. */
export const WORKING_WORD: Readonly<Record<string, string>> = {
  create: "Creating…",
  extend: "Extending…",
  extend_transcript: "Extending…",
};

export const workingWord = (kind: string): string => WORKING_WORD[kind] ?? "Working…";

/** What a control says when its job died and it did not say why. The wire carries a line for every
 *  ending; this is the floor under a build that does not. */
export const FAILED_FALLBACK = "That did not go through.";

/** THE TWO LINES A CONTROL SHOWS while its act is alive — its head (what the button's title slot
 *  says) and its line (what the meta slot says under it).
 *
 *  Queued has not started, so it keeps the control's own title and borrows the chat's exact phrase:
 *  one spelling of "queued behind the current turn" in the product, not two. Working replaces the
 *  title with the verb and counts steps in the same vocabulary as the job row (`jobs.stepsPhrase`).
 *  Failed keeps the title — the act is being offered again — and spends its line on the reason. */
export function actWords(rec: ActRec, title: string): { head: string; line: string } {
  if (rec.phase === "queued") return { head: title, line: QUEUED_LINE };
  if (rec.phase === "failed") return { head: title, line: rec.line || FAILED_FALLBACK };
  return { head: workingWord(rec.kind), line: [stepsPhrase(rec.steps), rec.label].filter(Boolean).join(" · ") };
}
