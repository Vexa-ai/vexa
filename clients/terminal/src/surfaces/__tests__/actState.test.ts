/** THE ACT'S OWN STATE — the bookkeeping under the control (Vexa-ai/vexa#1604).
 *
 *  Founder, 2026-09-06, having pressed "Create this page": *"this thing should indicate it's
 *  actually working"*. What the control WEARS is pinned next door (`minutes/__tests__/actWorking`),
 *  with the whole chat mounted and real job events; these are the two things underneath it that go
 *  quietly wrong and would go on being plausible if they did:
 *
 *  1. the transitions — a press that lands leaves nothing behind, a press that dies leaves exactly
 *     one line, and a job id that nobody is watching moves nothing;
 *  2. the TARGET — the only string joining a press to the job that answers it. It is spelled twice,
 *     once here and once in `control_plane/chat_intents.job_target`, and a control whose target is
 *     one character off never finds its own job and spins for ever.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  actEnded, actPressed, actQueued, actStarted, actStepped, actsNow, actWords, clearAct, endAct,
  pressAct, queueAct, resetActs, sendAct, settleAct, startAct, stepAct, workingWord, type Acts,
} from "../actState";
import { QUEUED_LINE, actTarget, stepsPhrase } from "../jobs";

const T = "desk/kg/plan.md";

beforeEach(() => { resetActs(); });

describe("the transitions", () => {
  it("a press is WORKING from the instant it is pressed — before the wire, before the job id", () => {
    const acts = pressAct({}, { target: T, kind: "create" });
    expect(acts[T]).toEqual({ kind: "create", phase: "working", steps: 0, label: "" });
    // an act with nothing to name is not an act
    expect(pressAct({}, { target: "", kind: "create" })).toEqual({});
  });

  it("mid-turn it QUEUES, in the chat row's own words", () => {
    let acts: Acts = pressAct({}, { target: T, kind: "extend" });
    acts = queueAct(acts, { target: T, kind: "extend" });
    expect(acts[T].phase).toBe("queued");
    expect(actWords(acts[T], "Extend this page")).toEqual({ head: "Extend this page", line: QUEUED_LINE });
  });

  it("…and stops saying so the moment it goes out, before any job id exists", () => {
    const queued: Acts = queueAct(pressAct({}, { target: T, kind: "extend" }), { target: T, kind: "extend" });
    const sent = sendAct(queued, T);
    expect(sent[T].phase).toBe("working");
    expect(actWords(sent[T], "x").head).toBe("Extending…");
    // a RUNNING act is not touched by the send that started it — its step count is not reset
    const running = stepAct(startAct(sent, { job: "j-1", kind: "extend", target: T }), "j-1", "Read");
    expect(sendAct(running, T)).toBe(running);
    expect(sendAct({}, T)).toEqual({});
  });

  it("the job hands the press its id, and steps are counted in the job row's vocabulary", () => {
    let acts: Acts = pressAct({}, { target: T, kind: "extend" });
    acts = startAct(acts, { job: "j-9", kind: "extend", target: T });
    expect(acts[T]).toEqual({ kind: "extend", phase: "working", job: "j-9", steps: 0, label: "" });
    acts = stepAct(acts, "j-9", "Read");
    acts = stepAct(acts, "j-9", "Write");
    expect(acts[T].steps).toBe(2);
    expect(actWords(acts[T], "x")).toEqual({ head: "Extending…", line: "2 steps · Write" });
    expect(actWords(stepAct(acts, "j-9", "Grep")[T], "x").line).toBe(`${stepsPhrase(3)} · Grep`);
  });

  it("SOMEBODY ELSE'S job moves nothing — a step or an ending for an id nobody here is watching", () => {
    const acts: Acts = startAct(pressAct({}, { target: T, kind: "extend" }), { job: "j-9", kind: "extend", target: T });
    expect(stepAct(acts, "j-other", "Read")).toBe(acts);
    expect(endAct(acts, "j-other", true, "done")).toBe(acts);
    expect(stepAct(acts, "", "Read")).toBe(acts);
  });

  it("LANDED leaves nothing behind — the page it wrote is the result", () => {
    const acts: Acts = startAct(pressAct({}, { target: T, kind: "create" }), { job: "j-9", kind: "create", target: T });
    expect(endAct(acts, "j-9", true, "kg/plan.md — written.")).toEqual({});
  });

  it("DIED leaves exactly one line, and it survives the settle that follows it", () => {
    const acts: Acts = startAct(pressAct({}, { target: T, kind: "create" }), { job: "j-9", kind: "create", target: T });
    const dead = endAct(acts, "j-9", false, "Writing kg/plan.md failed: the endpoint refused");
    expect(dead[T].phase).toBe("failed");
    expect(actWords(dead[T], "Create this page")).toEqual({
      head: "Create this page", line: "Writing kg/plan.md failed: the endpoint refused",
    });
    // the turn's own end must not tidy the reason away — nothing is running, but something is being said
    expect(settleAct(dead, T)).toBe(dead);
    // …and the press that answers it does clear it
    expect(clearAct(dead, T)).toEqual({});
  });

  it("a turn that ends without ever starting a job stops the control", () => {
    const acts: Acts = pressAct({}, { target: T, kind: "extend" });
    expect(settleAct(acts, T)).toEqual({});
    expect(settleAct({}, T)).toEqual({});
  });

  it("a second press REPLACES the failure it was pressed on, rather than stacking on it", () => {
    const dead = endAct(startAct(pressAct({}, { target: T, kind: "create" }), { job: "j-9", kind: "create", target: T }), "j-9", false, "nope");
    const again = pressAct(dead, { target: T, kind: "create" });
    expect(again[T]).toEqual({ kind: "create", phase: "working", steps: 0, label: "" });
  });

  it("two acts on two pages are two records, and one ending touches one of them", () => {
    let acts: Acts = startAct(pressAct({}, { target: "a.md", kind: "extend" }), { job: "j-1", kind: "extend", target: "a.md" });
    acts = startAct(pressAct(acts, { target: "b.md", kind: "create" }), { job: "j-2", kind: "create", target: "b.md" });
    acts = stepAct(acts, "j-2", "Grep");
    acts = endAct(acts, "j-1", true, "done");
    expect(Object.keys(acts)).toEqual(["b.md"]);
    expect(acts["b.md"].steps).toBe(1);
  });

  it("the word is the act's own — the person is owed the verb they pressed", () => {
    expect(workingWord("create")).toBe("Creating…");
    expect(workingWord("extend")).toBe("Extending…");
    expect(workingWord("extend_transcript")).toBe("Extending…");
  });
});

describe("the store the controls read", () => {
  it("runs a press through to a landing, and notifies nothing that did not change", () => {
    actPressed({ target: T, kind: "create" });
    expect(actsNow()[T].phase).toBe("working");
    actQueued({ target: T, kind: "create" });
    expect(actsNow()[T].phase).toBe("queued");
    actStarted({ job: "j-9", kind: "create", target: T });
    actStepped("j-9", "Write");
    expect(actsNow()[T]).toMatchObject({ job: "j-9", steps: 1, label: "Write" });
    const before = actsNow();
    actStepped("j-nobody", "Read");
    expect(actsNow()).toBe(before);          // an untouched map is the same map
    actEnded("j-9", true, "written");
    expect(actsNow()).toEqual({});
  });
});

describe("the target — the one string a press and its job share", () => {
  it("a page act names the workspace-qualified path, exactly as `job_target` does", () => {
    expect(actTarget({ kind: "extend", workspace: "175", path: "kg/plan.md" })).toBe("175/kg/plan.md");
    expect(actTarget({ kind: "create", path: "kg/plan.md" })).toBe("kg/plan.md");
  });

  it("a transcript passage names the ROOM and the words — it has no path to name", () => {
    // mirrors core/agent/tests/test_extend_transcript.py: meeting · “passage”
    expect(actTarget({ kind: "extend_transcript", meeting: "41", selection: "the pilot ships in March" }))
      .toBe("meeting 41 · “the pilot ships in March”");
    // whitespace is flattened, `]` (which would close the job mark server-side) is dropped
    expect(actTarget({ kind: "extend_transcript", meeting: "41", selection: "  the pilot\n ships]  " }))
      .toBe("meeting 41 · “the pilot ships”");
  });

  it("a long passage is capped at 60 the way the server caps it, ellipsis and all", () => {
    const long = "x".repeat(80);
    const t = actTarget({ kind: "extend_transcript", meeting: "7", selection: long });
    expect(t).toBe(`meeting 7 · “${"x".repeat(60)}…”`);
    // exactly 60 is not truncated
    expect(actTarget({ kind: "extend_transcript", meeting: "7", selection: "y".repeat(60) }))
      .toBe(`meeting 7 · “${"y".repeat(60)}”`);
  });

  it("two passages of one meeting are two targets; the same passage twice is one", () => {
    const a = actTarget({ kind: "extend_transcript", meeting: "41", selection: "first thing" });
    const b = actTarget({ kind: "extend_transcript", meeting: "41", selection: "second thing" });
    expect(a).not.toBe(b);
    expect(actTarget({ kind: "extend_transcript", meeting: "41", selection: "first thing", segment: "s9" })).toBe(a);
  });

  it("an act that is not a job has no target here", () => {
    expect(actTarget({ kind: "explore", term: "Kaar Tech", meeting: "41" })).toBe("");
    expect(actTarget({ kind: "highlight", meeting: "41" })).toBe("");
  });
});
