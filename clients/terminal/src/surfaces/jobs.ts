/** jobs — a long act the chat is NOT waiting on (Vexa-ai/vexa#1584).
 *
 *  Pressing Create or Extend used to hold the composer for as long as the act took: on 2026-09-06
 *  the founder pressed them four times, the agent made 38 tool calls, and he could not ask anything
 *  until each one landed. Those acts now run as background JOBS in the worker — the turn comes back
 *  with one line, and the job reports its own progress on the same event channel tagged with a job
 *  id (`core/agent/llm/JOBS.md`).
 *
 *  This file is the CHIP: the small record the CHAT renders while a job runs, and the three pure
 *  transitions over it. It is a function boundary rather than component state because that is the
 *  part worth testing — the rendering of "job · kg/plan.md · 3 steps · Write" is exactly the kind
 *  of string that goes quietly wrong.
 *
 *  It renders in the transcript, never in the composer: the input field is for typing, and a
 *  running act is told once, where the step rows are (founder ruling 2026-09-06, Vexa-ai/vexa#1587).
 *
 *  …AND ONCE MORE WHERE IT WAS PRESSED (Vexa-ai/vexa#1604). The control that started a job shows the
 *  same state in place; `surfaces/actState.ts` holds that, keyed by the target `actTarget` spells
 *  below. This file stays the place where a job is NAMED, so the row and the control name it alike.
 */
import type { ChatIntent, ChatIntentKind } from "./chatIntent";

export type JobRec = {
  id: string;
  kind: string;
  target: string;
  steps: number;
  /** the last step's short label — the same tail the turn's own live line shows */
  label: string;
  /** PRESSED, BUT NOT RUNNING YET — the chat is mid-turn and this act is waiting for it. A row and
   *  not silence: the founder pressed Extend while a turn was running and nothing happened at all —
   *  *"extend this page button does not work when chat is working"* (Vexa-ai/vexa#1594). Not a
   *  disabled control either: the act still fires, it just fires next.
   *
   *  Since Vexa-ai/vexa#1610 a queued row is also what a SERVER-HELD submission looks like, and what
   *  a same-target act waiting behind another looks like — three ways to be waiting, one row. */
  queued?: boolean;
  /** THIS ROW IS THE SERVER'S (Vexa-ai/vexa#1610) — it came from the session's pending list, not
   *  from a job this connection watched start. It is what `inbox.reconcileInbox` replaces wholesale
   *  on every refresh, and the reason a reload shows the same queue: nothing here is remembered. */
  inbox?: boolean;
  /** WHAT THIS ROW IS, in one word — `job` for an act, `queued` for a sentence somebody typed. A
   *  message is not a job and a row that called it one would be the only thing on screen saying so. */
  noun?: string;
};

export function startJob(jobs: JobRec[], j: { id: string; kind: string; target: string }): JobRec[] {
  if (!j.id || jobs.some((x) => x.id === j.id)) return jobs;
  return [...jobs, { id: j.id, kind: j.kind, target: j.target, steps: 0, label: "" }];
}

/** WHAT A QUEUED ROW SAYS. One phrase, written here, because it is the whole of what the person
 *  is told while they wait — and the founder's ruling names it: the act fires when pressed, and
 *  when the turn in front of it has to finish first, the panel says so. */
export const QUEUED_LINE = "queued behind the current turn";

/** The row a PRESS puts on screen before there is a job id to put on it (Vexa-ai/vexa#1594). Its
 *  `id` is the client's own — the real `job-started` hands the row its server id, so one line runs
 *  from the press to the page landing rather than one line stopping and another starting. */
export function queueJob(jobs: JobRec[], j: { id: string; kind: string; target: string; inbox?: boolean }): JobRec[] {
  if (!j.id || jobs.some((x) => x.id === j.id)) return jobs;
  return [...jobs, { id: j.id, kind: j.kind, target: j.target, steps: 0, label: "", queued: true,
                     ...(j.inbox ? { inbox: true } : {}) }];
}

/** THE QUEUED ROW BECOMES THE RUNNING ONE (Vexa-ai/vexa#1610), in place.
 *
 *  A same-target act now WAITS for the one in front of it instead of being refused, and it is given
 *  its job id while it waits — so when it finally starts, the row already on screen is the row that
 *  starts. Two rows for one act would read as two acts, which is the same lie #1594 removed one
 *  step earlier in the act's life. */
export function promoteJob(jobs: JobRec[], id: string): JobRec[] {
  return jobs.map((j) => (j.id === id && j.queued ? { ...j, queued: undefined } : j));
}

/** THE ONE THING AN ACT IS ABOUT — the workspace-qualified page path, spelled exactly as the server
 *  spells it (`control_plane/chat_intents.job_target`). The queued row and the job's own row name
 *  the same page because they name it the same way, not because they happen to agree. */
export function jobTarget(i: { workspace?: string; path?: string }): string {
  const ws = (i.workspace ?? "").trim();
  const path = (i.path ?? "").trim();
  return ws && path ? `${ws}/${path}` : (path || ws);
}

/** THE ACTS THAT RUN AS JOBS. Mirrors `chat_intents.JOB_KINDS`, and closed for the same reason it is
 *  closed there: whether an act runs in the background is a property of the act, never a flag. */
export const JOB_KINDS: ReadonlySet<ChatIntentKind> = new Set<ChatIntentKind>(["create", "extend", "extend_transcript"]);

export const isJobIntent = (i: ChatIntent): boolean => JOB_KINDS.has(i.kind);

/** How much of a selected passage NAMES the act it started. Mirrors
 *  `chat_intents.TARGET_SELECTION_MAX` — the number has to be the same one, because this is the
 *  string the control matches its job by. */
export const TARGET_SELECTION_MAX = 60;

/** A selected passage as a target can carry it — the client's copy of `chat_intents._passage`.
 *  `]` closes the job mark server-side and is dropped there, so it is dropped here too: a target
 *  that differs by one character is a control that never finds its own job. Sliced by CODE POINT,
 *  which is what Python's `[:60]` does. */
function passage(selection: string): string {
  const flat = String(selection ?? "").replace(/]/g, "").split(/\s+/).filter(Boolean).join(" ");
  const cp = [...flat];
  return cp.slice(0, TARGET_SELECTION_MAX).join("").trim() + (cp.length > TARGET_SELECTION_MAX ? "…" : "");
}

/** THE NAME AN ACT AND ITS JOB SHARE (Vexa-ai/vexa#1604) — the client's spelling of
 *  `chat_intents.job_target`, for every kind that runs as a job.
 *
 *  The control that was pressed has an intent; the `job-started` event that comes back has a target
 *  string and nothing else in common with it. This is the only thing that joins them, so it is
 *  written once and spelled exactly as the server spells it — including the transcript form, where
 *  what the person acted on is the meeting and the words, because a passage has no path. */
export function actTarget(intent: ChatIntent): string {
  if (intent.kind === "extend_transcript") {
    const meeting = String(intent.meeting ?? "").trim();
    const quote = passage(intent.selection);
    if (meeting && quote) return `meeting ${meeting} · “${quote}”`;
    return meeting ? `meeting ${meeting}` : quote;
  }
  if (intent.kind === "extend" || intent.kind === "create") return jobTarget(intent);
  return "";
}

export function stepJob(jobs: JobRec[], id: string, label: string): JobRec[] {
  return jobs.map((j) => (j.id === id ? { ...j, steps: j.steps + 1, label } : j));
}

/** THE JOB SAID SOMETHING ABOUT ITSELF (Vexa-ai/vexa#1613) — it reached a window budget, checkpointed
 *  and carried on. The label changes; the step count does not, because no step was taken. It is the
 *  only line in the product that reports a job is STILL running rather than what it is doing. */
export function noteJob(jobs: JobRec[], id: string, label: string): JobRec[] {
  if (!label) return jobs;
  return jobs.map((j) => (j.id === id ? { ...j, label } : j));
}

export function endJob(jobs: JobRec[], id: string): JobRec[] {
  return jobs.filter((j) => j.id !== id);
}

/** HOW FAR ALONG, IN WORDS. One spelling, because the chat's row and the control that was pressed
 *  both count steps and a person reading both must not have to notice they agree. */
export const stepsPhrase = (steps: number): string => (steps ? `${steps} step${steps === 1 ? "" : "s"}` : "");

/** What the CHAT says while jobs run — one line per job, at the foot of the transcript where the
 *  turn's own step rows are.
 *
 *  The shape mirrors the step row on purpose (`Reading · plan.md · 3 steps`): a person watching the
 *  bottom of the chat should not have to learn a second vocabulary to read what the agent is doing
 *  when it happens to be doing it in the background. What differs is the NAME of the thing — a job
 *  is about one target, and that is the only way to tell two of them apart. */
export function jobLine(jobs: JobRec[]): string {
  return jobs
    .map((j) =>
      // A QUEUED act has no steps to count and no label to show — it has not started. What it has
      // is the reason it has not, which is the only thing the person needs while they wait.
      (j.queued
        ? [j.noun || "job", j.target || j.kind, QUEUED_LINE]
        : [j.noun || "job", j.target || j.kind, stepsPhrase(j.steps), j.label])
        .filter(Boolean)
        .join(" · "),
    )
    .join("   ");
}
