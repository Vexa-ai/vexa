/** jobs — a long act the chat is NOT waiting on (Vexa-ai/vexa#1584).
 *
 *  Pressing Create or Extend used to hold the composer for as long as the act took: on 2026-09-06
 *  the founder pressed them four times, the agent made 38 tool calls, and he could not ask anything
 *  until each one landed. Those acts now run as background JOBS in the worker — the turn comes back
 *  with one line, and the job reports its own progress on the same event channel tagged with a job
 *  id (`core/agent/llm/JOBS.md`).
 *
 *  This file is the CHIP: the small record the composer renders while a job runs, and the three
 *  pure transitions over it. It is a function boundary rather than component state because that is
 *  the part worth testing — the rendering of "job · kg/plan.md · 3 steps · Write" is exactly the
 *  kind of string that goes quietly wrong.
 */

export type JobRec = {
  id: string;
  kind: string;
  target: string;
  steps: number;
  /** the last step's short label — the same tail the turn's own live line shows */
  label: string;
};

export function startJob(jobs: JobRec[], j: { id: string; kind: string; target: string }): JobRec[] {
  if (!j.id || jobs.some((x) => x.id === j.id)) return jobs;
  return [...jobs, { id: j.id, kind: j.kind, target: j.target, steps: 0, label: "" }];
}

export function stepJob(jobs: JobRec[], id: string, label: string): JobRec[] {
  return jobs.map((j) => (j.id === id ? { ...j, steps: j.steps + 1, label } : j));
}

export function endJob(jobs: JobRec[], id: string): JobRec[] {
  return jobs.filter((j) => j.id !== id);
}

/** What the composer says while jobs run — one line per job, beside the turn's own live state.
 *
 *  The shape mirrors the turn line on purpose (`working · 18 steps · entity_upsert`): a person
 *  watching the bottom of the chat should not have to learn a second vocabulary to read what the
 *  agent is doing when it happens to be doing it in the background. What differs is the NAME of the
 *  thing — a job is about one target, and that is the only way to tell two of them apart. */
export function jobLine(jobs: JobRec[]): string {
  return jobs
    .map((j) =>
      ["job", j.target || j.kind, j.steps ? `${j.steps} step${j.steps === 1 ? "" : "s"}` : "", j.label]
        .filter(Boolean)
        .join(" · "),
    )
    .join("   ");
}
