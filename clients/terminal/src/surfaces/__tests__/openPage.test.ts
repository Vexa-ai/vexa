/** open — the agent putting a page in front of the person (Vexa-ai/vexa#1586).
 *
 *  The founder typed "open meeting transcript". The agent read 677 segments, described them, and
 *  offered to re-verify facts: *"it did not open the transcript"*. This is the client half of the
 *  fix, and there are exactly two things in it that can break silently — the reader dropping an
 *  event it does not know, and the panel treating an ASK like a suggestion.
 */
import { describe, it, expect } from "vitest";
import { streamChatTurn, type ChatStreamCallbacks } from "../chatStream";
import { pageForArtifact } from "../../minutes/roomView";

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

function ev(o: Record<string, unknown>): string {
  return `data: ${JSON.stringify(o)}\n\n`;
}

function recorder() {
  const state = {
    opened: [] as { workspace: string; path: string; target: string }[],
    artifacts: [] as { workspace: string; path: string; focus: boolean; pin: boolean }[],
    jobStarted: [] as string[],
  };
  const cb: ChatStreamCallbacks = {
    onStarting: () => {},
    onDelta: () => {},
    onTool: () => {},
    onCommit: () => {},
    onRejected: () => {},
    onModelFailure: () => {},
    onError: () => {},
    onArtifact: (a) => { state.artifacts.push(a); },
    onOpen: (o) => { state.opened.push(o); },
    onJobStarted: (j) => { state.jobStarted.push(j.jobId); },
  };
  return { state, cb };
}

const noWait = { now: () => 0, sleep: async () => {}, reconnectBackoffMs: 0 };

async function run(chunks: string[]) {
  const { state, cb } = recorder();
  const fetchImpl = (async () => sseResponse(chunks)) as unknown as typeof fetch;
  await streamChatTurn({ prompt: "open meeting transcript", session: "meet-147", active: null },
    cb, { ...noWait, fetchImpl, signal: new AbortController().signal });
  return state;
}

describe("the open event on the chat stream", () => {
  it("forwards the resolved slot the tool answered with", async () => {
    const s = await run([
      ev({ type: "open", target: "meeting:transcript", workspace: "", path: "meeting:147" }),
      ev({ type: "turn-complete" }),
    ]);
    expect(s.opened).toEqual([{ workspace: "", path: "meeting:147", target: "meeting:transcript" }]);
  });

  it("is NOT an artifact — the two events reach two different listeners", async () => {
    // An artifact stands down in front of a reader who has opened something else; an open is that
    // reader. Folding them would put one flag in charge of both, and it would be wrong for one.
    const s = await run([
      ev({ type: "open", target: "notes.md", workspace: "team", path: "notes.md" }),
      ev({ type: "turn-complete" }),
    ]);
    expect(s.artifacts).toEqual([]);
    expect(s.opened).toHaveLength(1);
  });

  it("ignores an open with nothing to open", async () => {
    const s = await run([ev({ type: "open", target: "meeting:note" }), ev({ type: "turn-complete" })]);
    expect(s.opened).toEqual([]);
  });

  it("honours one a background job produced, and only the job it started", async () => {
    // A job may open a page too. The rule is the job lane's own: this connection acts on the jobs
    // it watched start, and a foreign job on the shared Stream is not its business.
    const s = await run([
      ev({ type: "job-started", job_id: "j1", kind: "extend", target: "kg/plan.md", line: "on it" }),
      ev({ type: "open", job_id: "j1", target: "kg/plan.md", workspace: "", path: "kg/plan.md" }),
      ev({ type: "open", job_id: "OTHER", target: "x.md", workspace: "", path: "x.md" }),
      ev({ type: "job-done", job_id: "j1", line: "done" }),
      ev({ type: "turn-complete" }),
    ]);
    expect(s.opened.map((o) => o.path)).toEqual(["kg/plan.md"]);
  });
});

describe("what the panel does with it", () => {
  it("resolves both dialects through the one function the artifact event already uses", () => {
    // the transcript is a CANVAS bound to a row id, never a file (founder ruling 2026-09-01)
    expect(pageForArtifact({ workspace: "", path: "meeting:147" }))
      .toEqual({ kind: "meeting", path: "147", label: "Transcript" });
    expect(pageForArtifact({ workspace: "team", path: "kg/entities/meeting/2026-03-02-tsc.md" }))
      .toEqual({ path: "kg/entities/meeting/2026-03-02-tsc.md", slug: "team", label: "2026-03-02-tsc" });
  });
});

/*  THE STANDING CHIPS ARE GONE (Vexa-ai/vexa#1600). The other half of #1586 stood here: a row above
 *  the composer offering this meeting's transcript and note, because `×` on the transcript tab had
 *  left the founder with no way back to it. Shown that row, he ruled on the cause — *"just keep a
 *  tab that can't be closed instead"* — so the tab cannot be closed and the offer has nothing left
 *  to recover. The agent's `open` event above is untouched: being ABLE to open a page when asked is
 *  the half of #1586 that was never about the chips. Successor: `minutes/__tests__/permanentTab`.  */
