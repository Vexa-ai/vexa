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
 */

export type JobRec = {
  id: string;
  kind: string;
  target: string;
  steps: number;
  /** the last step's short label — the same tail the turn's own live line shows */
  label: string;
  /** PRESSED, BUT NOT SENT YET (Vexa-ai/vexa#1594) — the chat is mid-turn and this act is waiting
   *  for it. A row and not silence: the founder pressed Extend while a turn was running and nothing
   *  happened at all — *"extend this page button does not work when chat is working"*. Not a
   *  disabled control either: the act still fires, it just fires next. */
  queued?: boolean;
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
export function queueJob(jobs: JobRec[], j: { id: string; kind: string; target: string }): JobRec[] {
  if (!j.id || jobs.some((x) => x.id === j.id)) return jobs;
  return [...jobs, { id: j.id, kind: j.kind, target: j.target, steps: 0, label: "", queued: true }];
}

/** THE ONE THING AN ACT IS ABOUT — the workspace-qualified page path, spelled exactly as the server
 *  spells it (`control_plane/chat_intents.job_target`). The queued row and the job's own row name
 *  the same page because they name it the same way, not because they happen to agree. */
export function jobTarget(i: { workspace?: string; path?: string }): string {
  const ws = (i.workspace ?? "").trim();
  const path = (i.path ?? "").trim();
  return ws && path ? `${ws}/${path}` : (path || ws);
}

export function stepJob(jobs: JobRec[], id: string, label: string): JobRec[] {
  return jobs.map((j) => (j.id === id ? { ...j, steps: j.steps + 1, label } : j));
}

export function endJob(jobs: JobRec[], id: string): JobRec[] {
  return jobs.filter((j) => j.id !== id);
}

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
        ? ["job", j.target || j.kind, QUEUED_LINE]
        : ["job", j.target || j.kind, j.steps ? `${j.steps} step${j.steps === 1 ? "" : "s"}` : "", j.label])
        .filter(Boolean)
        .join(" · "),
    )
    .join("   ");
}
