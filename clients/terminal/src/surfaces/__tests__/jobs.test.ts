/** jobs — a long act the chat is not waiting on (Vexa-ai/vexa#1584).
 *
 *  Two halves, both of which fail silently if they break: the CHIP (what the composer says while a
 *  job runs) and the STREAM (which events belong to a job and which to the turn). The second is the
 *  one worth pinning hardest — a job's events arrive on the same connection as the turn's, and
 *  folding them into the turn would climb the step count of a bubble the person finished reading a
 *  minute ago, and end the read while the job was still writing.
 */
import { describe, it, expect, vi } from "vitest";
import { endJob, jobLine, jobTarget, queueJob, startJob, stepJob, type JobRec } from "../jobs";
import { streamChatTurn, type ChatStreamCallbacks } from "../chatStream";

function sseResponse(chunks: string[]): Response {
  const enc = new TextEncoder();
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(enc.encode(chunks[i++]));
      else controller.close();
    },
  });
  return { ok: true, status: 200, body } as unknown as Response;
}

function ev(o: Record<string, unknown>, id?: string): string {
  return (id ? `id: ${id}\n` : "") + `data: ${JSON.stringify(o)}\n\n`;
}

function recorder() {
  const state = {
    text: "", tools: [] as string[], commit: undefined as string | undefined,
    jobStarted: [] as { jobId: string; kind: string; target: string; line: string }[],
    jobSteps: [] as string[], jobEnded: [] as { jobId: string; ok: boolean; line: string }[],
    lost: "",
  };
  const cb: ChatStreamCallbacks = {
    onStarting: () => {},
    onDelta: (t) => { state.text += t; },
    onTool: (t) => { state.tools.push(t); },
    onCommit: (sha) => { state.commit = sha; },
    onRejected: () => {},
    onModelFailure: () => {},
    onError: (m) => { state.lost += m; },
    onJobStarted: (j) => { state.jobStarted.push(j); },
    onJobStep: (_id, tool) => { state.jobSteps.push(tool); },
    onJobEnd: (j) => { state.jobEnded.push(j); },
  };
  return { state, cb };
}

const noWait = { now: () => 0, sleep: async () => {}, reconnectBackoffMs: 0 };

describe("the job chip", () => {
  const j = (over: Partial<JobRec> = {}): JobRec => ({ id: "j-1", kind: "extend", target: "kg/plan.md", steps: 0, label: "", ...over });

  it("says nothing when nothing is running", () => {
    expect(jobLine([])).toBe("");
  });

  it("names the TARGET, because that is what tells two jobs apart", () => {
    expect(jobLine([j()])).toBe("job · kg/plan.md");
    expect(jobLine([j({ steps: 1, label: "plan.md" })])).toBe("job · kg/plan.md · 1 step · plan.md");
    expect(jobLine([j({ steps: 4, label: "Grep" })])).toBe("job · kg/plan.md · 4 steps · Grep");
  });

  it("falls back to the kind when there is no target to name", () => {
    expect(jobLine([j({ target: "" })])).toBe("job · extend");
  });

  // ── PRESSED WHILE THE CHAT WAS WORKING (Vexa-ai/vexa#1594) ─────────────────────────────────────
  //
  //  *"extend this page button does not work when chat is working"*. A press mid-turn used to reach
  //  nothing at all; it now reaches THIS row, which is the only thing standing between the founder
  //  and a control that silently does nothing when he presses it.

  it("a queued act says why it has not started, and counts no steps it has not taken", () => {
    expect(jobLine([j({ queued: true })])).toBe("job · kg/plan.md · queued behind the current turn");
    // a step count on a job that has not begun would read as progress
    expect(jobLine([j({ queued: true, steps: 3, label: "Grep" })]))
      .toBe("job · kg/plan.md · queued behind the current turn");
  });

  it("queueJob adds exactly one row per press, and never a second for the same one", () => {
    const one = queueJob([], { id: "q-1", kind: "extend", target: "kg/plan.md" });
    expect(one).toHaveLength(1);
    expect(one[0].queued).toBe(true);
    expect(queueJob(one, { id: "q-1", kind: "extend", target: "kg/plan.md" })).toBe(one);
    expect(queueJob(one, { id: "", kind: "extend", target: "x" })).toBe(one);
  });

  it("the queued row hands over to the job: endJob removes it, startJob puts the real one there", () => {
    const queued = queueJob([], { id: "q-1", kind: "create", target: "kg/new.md" });
    const running = startJob(endJob(queued, "q-1"), { id: "j-9", kind: "create", target: "kg/new.md" });
    expect(running).toHaveLength(1);                       // ONE act, ONE line
    expect(running[0].id).toBe("j-9");
    expect(running[0].queued).toBeUndefined();
    expect(jobLine(running)).toBe("job · kg/new.md");
  });

  it("names the page the way the SERVER names it (chat_intents.job_target)", () => {
    // the queued row and the job's own row are about visibly the same page because they are
    // spelled by the same rule, not because they happen to look alike
    expect(jobTarget({ workspace: "desk", path: "kg/plan.md" })).toBe("desk/kg/plan.md");
    expect(jobTarget({ path: "kg/plan.md" })).toBe("kg/plan.md");        // no slug = the own desk
    expect(jobTarget({ workspace: "desk" })).toBe("desk");
    expect(jobTarget({})).toBe("");
  });

  it("shows several at once — concurrent jobs are the normal case", () => {
    const two = startJob(startJob([], { id: "j-1", kind: "extend", target: "a.md" }), { id: "j-2", kind: "create", target: "b.md" });
    expect(jobLine(two)).toBe("job · a.md   job · b.md");
  });

  it("counts steps per job and forgets a job that ended", () => {
    let jobs = startJob([], { id: "j-1", kind: "extend", target: "a.md" });
    jobs = startJob(jobs, { id: "j-2", kind: "create", target: "b.md" });
    jobs = stepJob(jobs, "j-1", "Read");
    jobs = stepJob(jobs, "j-1", "Write");
    jobs = stepJob(jobs, "j-2", "Grep");
    expect(jobs.map((x) => x.steps)).toEqual([2, 1]);
    expect(jobLine(endJob(jobs, "j-1"))).toBe("job · b.md · 1 step · Grep");
    // starting the same id twice is a no-op — a reconnect must not double the chip
    expect(startJob(jobs, { id: "j-1", kind: "extend", target: "a.md" })).toBe(jobs);
  });
});

describe("streamChatTurn — a turn that spawns a job", () => {
  it("reports the job, keeps reading past turn-complete, and lands the result", async () => {
    // The shape the worker emits: ack, the one line, job-started, turn-complete — then the job's
    // own work, minutes later, on the same connection.
    const fetchImpl = vi.fn().mockResolvedValue(sseResponse([
      ev({ type: "turn-accepted", turn_id: "t1" }, "1-0"),
      ev({ type: "message-delta", text: "Extending kg/plan.md — I'll say when it's there.", turn_id: "t1" }, "2-0"),
      ev({ type: "job-started", job_id: "j-9", kind: "extend", target: "kg/plan.md", line: "Extending kg/plan.md — I'll say when it's there.", turn_id: "t1" }, "3-0"),
      ev({ type: "turn-complete", turn_id: "t1" }, "4-0"),
      ev({ type: "tool-call", tool: "Read", job_id: "j-9" }, "5-0"),
      ev({ type: "tool-call", tool: "Write", job_id: "j-9" }, "6-0"),
      ev({ type: "commit", sha: "abc123", job_id: "j-9" }, "7-0"),
      ev({ type: "job-done", job_id: "j-9", ok: true, line: "kg/plan.md — extended." }, "8-0"),
    ]));
    const { state, cb } = recorder();

    const result = await streamChatTurn(
      { prompt: "Extend: kg/plan.md", session: "s1", active: undefined },
      cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, ...noWait },
    );

    expect(state.jobStarted).toEqual([{ jobId: "j-9", kind: "extend", target: "kg/plan.md", line: "Extending kg/plan.md — I'll say when it's there." }]);
    // the acknowledgement is the turn's only prose, and the turn's own step count stays at zero:
    // the job's tool calls are the JOB's, never this bubble's.
    expect(state.text).toBe("Extending kg/plan.md — I'll say when it's there.");
    expect(state.tools).toEqual([]);
    expect(state.jobSteps).toEqual(["Read", "Write"]);
    // the commit is shared verbatim — it is what makes the written page refresh itself
    expect(state.commit).toBe("abc123");
    expect(state.jobEnded).toEqual([{ jobId: "j-9", ok: true, line: "kg/plan.md — extended." }]);
    expect(result.terminal).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(1);   // turn-complete did NOT end the read
  });

  it("posts a line when the job fails — never silence", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(sseResponse([
      ev({ type: "job-started", job_id: "j-1", kind: "create", target: "a.md", line: "Writing a.md — I'll say when it's there." }, "1-0"),
      ev({ type: "turn-complete", turn_id: "t1" }, "2-0"),
      ev({ type: "job-failed", job_id: "j-1", line: "Writing a.md failed: the endpoint refused" }, "3-0"),
    ]));
    const { state, cb } = recorder();

    await streamChatTurn({ prompt: "Create: a.md", session: "s1", active: undefined }, cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, ...noWait });

    expect(state.jobEnded).toEqual([{ jobId: "j-1", ok: false, line: "Writing a.md failed: the endpoint refused" }]);
  });

  it("ignores a job that is not this connection's", async () => {
    // Two turns of one thread read the SAME output Stream. A turn that started no job must not
    // render another one's steps, and a turn that started one must not render a third's.
    const fetchImpl = vi.fn().mockResolvedValue(sseResponse([
      ev({ type: "tool-call", tool: "Read", job_id: "j-other" }, "1-0"),
      ev({ type: "job-done", job_id: "j-other", ok: true, line: "somebody else's" }, "2-0"),
      ev({ type: "message-delta", text: "mine", turn_id: "t2" }, "3-0"),
      ev({ type: "turn-complete", turn_id: "t2" }, "4-0"),
    ]));
    const { state, cb } = recorder();

    const result = await streamChatTurn({ prompt: "hi", session: "s1", active: undefined }, cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, ...noWait });

    expect(state.jobStarted).toEqual([]);
    expect(state.jobSteps).toEqual([]);
    expect(state.jobEnded).toEqual([]);
    expect(state.text).toBe("mine");
    expect(result.terminal).toBe(true);
  });

  it("watches BOTH jobs when one turn spawns two, and ends only when both have landed", async () => {
    // A marked act spawns exactly one; a turn that calls `spawn_job` twice spawns two, and a second
    // job nobody was watching would land its page with no line saying where it came from.
    const fetchImpl = vi.fn().mockResolvedValue(sseResponse([
      ev({ type: "job-started", job_id: "j-1", kind: "research", target: "Acme" }, "1-0"),
      ev({ type: "job-started", job_id: "j-2", kind: "research", target: "Globex" }, "2-0"),
      ev({ type: "turn-complete", turn_id: "t1" }, "3-0"),
      ev({ type: "tool-call", tool: "WebSearch", job_id: "j-2" }, "4-0"),
      ev({ type: "job-done", job_id: "j-1", ok: true, line: "Acme — done." }, "5-0"),
      ev({ type: "job-done", job_id: "j-2", ok: true, line: "Globex — done." }, "6-0"),
    ]));
    const { state, cb } = recorder();

    const result = await streamChatTurn({ prompt: "look at both", session: "s1", active: undefined }, cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, ...noWait });

    expect(state.jobStarted.map((j) => j.target)).toEqual(["Acme", "Globex"]);
    expect(state.jobSteps).toEqual(["WebSearch"]);
    expect(state.jobEnded.map((j) => j.line)).toEqual(["Acme — done.", "Globex — done."]);
    expect(result.terminal).toBe(true);
  });

  it("a turn that spawned nothing still ends on turn-complete", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(sseResponse([
      ev({ type: "message-delta", text: "hello", turn_id: "t1" }, "1-0"),
      ev({ type: "turn-complete", turn_id: "t1" }, "2-0"),
    ]));
    const { state, cb } = recorder();
    const result = await streamChatTurn({ prompt: "hi", session: "s1", active: undefined }, cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, ...noWait });
    expect(state.text).toBe("hello");
    expect(result.terminal).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("a refusal is the turn's own line — no chip, nothing to wait for", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(sseResponse([
      ev({ type: "job-refused", kind: "extend", target: "kg/plan.md", line: "There is already something running on kg/plan.md — I'll finish that one first.", turn_id: "t1" }, "1-0"),
      ev({ type: "message-delta", text: "There is already something running on kg/plan.md — I'll finish that one first.", turn_id: "t1" }, "2-0"),
      ev({ type: "turn-complete", turn_id: "t1" }, "3-0"),
    ]));
    const { state, cb } = recorder();

    const result = await streamChatTurn({ prompt: "Extend: kg/plan.md", session: "s1", active: undefined }, cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, ...noWait });

    expect(state.jobStarted).toEqual([]);
    expect(state.text).toContain("already something running on kg/plan.md");
    expect(result.terminal).toBe(true);
  });
});
