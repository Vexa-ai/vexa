/** SetupGate — what a fresh instance's administrator meets, and what it refuses to do.
 *
 *  The wizard is gone (founder ruling 2026-09-02, second pass): there are no model/transcription
 *  steps in front of the company-setup conversation any more, so the shape under test here is
 *  two phases and one card —
 *    "opening"  the handoff marker is absent → write it, then navigate to the setup chat,
 *    "card"     the workbench is live underneath and this is a corner readout.
 *
 *  Three properties, all of them silent when they break:
 *    • a RELOAD mid-conversation resumes as the card, never as a fresh hand-off;
 *    • `setup.completed` is written by exactly ONE thing — the card's "Open this Vexa", which only
 *      appears after the SERVER says the layer is complete;
 *    • a hand-off whose write FAILED does not navigate. Navigating anyway strands the admin in the
 *      loop the ruling exists to close, with nothing on screen saying why.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { SetupGate, companyLayerStatus, setupResumePhase, shouldShowSetup } from "../SetupGate";

/** The hand-off ends in a real navigation, and jsdom can neither perform one nor be made to report
 *  one: `window.location` is [Unforgeable], so a `vi.spyOn(window.location, "assign")` installs a
 *  spy on an object the component never calls and records zero calls forever — which reads as
 *  "it did not navigate" whether or not it did, and would make the ruling-3 test pass vacuously.
 *
 *  So the tests below assert the two things that ARE observable, and between them they pin the
 *  ordering the ruling is about:
 *    • the PUT that persists the marker, and
 *    • REACHED_NAVIGATE — the session flag `handOff` writes on the line immediately before the
 *      navigation. Present ⇒ the write resolved and we went; absent ⇒ we stopped, which is the
 *      whole of ruling 3. */
const REACHED_NAVIGATE = "vexa.setupHandoffAttempted";
const reachedNavigate = () => sessionStorage.getItem(REACHED_NAVIGATE) === "1";

beforeEach(() => {
  try { vi.spyOn(window.location, "assign").mockImplementation(() => {}); } catch { /* left as a jsdom warning */ }
  try { sessionStorage.clear(); } catch { /* ignore */ }
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

type Stub = {
  setup?: { status: number; value?: Record<string, string> };
  global?: Record<string, unknown>;
  /** Per-call global states, so a test can make the verifier change its mind between polls. */
  globalSeq?: Record<string, unknown>[];
  setupPutFails?: boolean;
};

/** One fetch double for every probe the component uses: the platform-settings `setup` key, the
 *  company layer's live state, and the two config test edges the card's last line probes. Returns
 *  the recorded calls so a test can assert what was PERSISTED. */
function stubProbes(s: Stub) {
  const calls: { url: string; method: string; body?: string }[] = [];
  let globalReads = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      calls.push({ url: u, method: init?.method || "GET", body: init?.body as string });
      if (u.includes("/api/admin/settings/setup")) {
        const cfg = s.setup ?? { status: 200, value: {} };
        if (init?.method === "PUT") {
          if (s.setupPutFails) return new Response(JSON.stringify({ error: "field not allowed" }), { status: 400 });
          return new Response(JSON.stringify({ key: "setup", value: {} }), { status: 200 });
        }
        if (cfg.status === 404) return new Response(null, { status: 404 });
        return new Response(JSON.stringify({ key: "setup", value: cfg.value ?? {} }), { status: 200 });
      }
      if (u.includes("/api/global/state")) {
        const seq = s.globalSeq;
        const body = seq ? (seq[Math.min(globalReads, seq.length - 1)]) : (s.global ?? {
          global_setup: "missing", company: null, present: [],
          missing_files: ["README.md"], reasons: ["_global has no README.md yet."],
        });
        globalReads += 1;
        return new Response(JSON.stringify(body), { status: 200 });
      }
      // the card's model/transcription probe — green by default so it renders nothing
      return new Response(JSON.stringify({ ok: true, summary: "stub" }), { status: 200 });
    }),
  );
  return calls;
}

describe("shouldShowSetup", () => {
  it("null (non-admin probe 404) → hidden", () => expect(shouldShowSetup(null)).toBe(false));
  it("completed → hidden", () => expect(shouldShowSetup({ completed: "true" })).toBe(false));
  it("fresh / partial → shown", () => {
    expect(shouldShowSetup({})).toBe(true);
    expect(shouldShowSetup({ models: "done" })).toBe(true);
  });
});

describe("setupResumePhase — where a reload lands", () => {
  it("non-admin → hidden", () => expect(setupResumePhase(null)).toBe("hidden"));
  it("completed → hidden", () => expect(setupResumePhase({ completed: "true" })).toBe("hidden"));
  it("fresh → opening (straight at the conversation, no wizard in front of it)", () => {
    expect(setupResumePhase({})).toBe("opening");
  });
  it("already handed off → the card, NOT a second hand-off", () => {
    expect(setupResumePhase({ global: "handoff" })).toBe("card");
  });
  it("completed WINS over a stale handoff marker", () => {
    // Otherwise an instance that finished setup would put the corner card back on every load.
    expect(setupResumePhase({ global: "handoff", completed: "true" })).toBe("hidden");
  });
});

/** RULING 2 — the card states a STATE in the company's words. The founder, shown
 *  "✓ README.md ✓ PRINCIPLES.md ✓ OBJECTIVES.md ○ STRUCTURE.md ○ MISSING.md": "this does not seem
 *  to me like a clear state." No filename may appear in either sentence, at any count. */
describe("companyLayerStatus — the derived sentences", () => {
  const five = ["README.md", "PRINCIPLES.md", "OBJECTIVES.md", "STRUCTURE.md", "MISSING.md"];
  const at = (n: number) => companyLayerStatus({ present: five.slice(0, n), missing_files: five.slice(n) });

  it("0 of 5 — nothing written, and the first thing to write is named", () => {
    const s = at(0);
    expect(s.where).toBe("Company layer: 0 of 5. Nothing written yet.");
    expect(s.next).toBe(
      "Now: identity — who you are. " +
      "Then: how you work, what you are working toward, who does what and who can see what, what is not yet known. " +
      "When all of them are written the instance opens: other people can sign in, and mails start going out.",
    );
  });

  it("3 of 5 — the founder's own example, word for word", () => {
    const s = at(3);
    expect(s.where).toBe("Company layer: 3 of 5. Who you are, how you work, what you are working toward: written.");
    expect(s.next).toBe(
      "Now: structure — who does what and who can see what. " +
      "Then: what is not yet known. " +
      "When both are written the instance opens: other people can sign in, and mails start going out.",
    );
  });

  it("4 of 5 — one left is 'that', never 'both'", () => {
    expect(at(4).next).toContain("When that is written the instance opens");
  });

  it("5 of 5, verifier not satisfied — written is not accepted", () => {
    const s = at(5);
    expect(s.where).toBe(
      "Company layer: 5 of 5. Who you are, how you work, what you are working toward, " +
      "who does what and who can see what, what is not yet known: written.",
    );
    expect(s.next).toContain("The agent checks it itself");
  });

  it("5 of 5, accepted — the sentence becomes what the button will do", () => {
    expect(companyLayerStatus({ present: five, missing_files: [] }, true).next)
      .toBe("Other people can sign in, and mails start going out.");
  });

  it("NO FILENAME survives into either sentence, at any count", () => {
    for (let n = 0; n <= 5; n += 1) {
      const s = at(n);
      for (const f of five) {
        expect(s.where).not.toContain(f);
        expect(s.next).not.toContain(f);
      }
      expect(s.where).not.toContain(".md");
      expect(s.next).not.toContain(".md");
    }
  });

  it("the count is the SERVER's, not a constant — a sixth file changes the sentence on its own", () => {
    const s = companyLayerStatus({ present: five, missing_files: ["CHARTER.md"] });
    expect(s.where).toContain("5 of 6");
    expect(s.next).toContain("Now: charter");
    expect(s.next).toContain("When that is written");
  });

  it("nothing read yet says so rather than claiming zero", () => {
    expect(companyLayerStatus(null).where).toBe("Reading this instance…");
  });
});

describe("SetupGate", () => {
  it("non-admin falls through to the workbench", async () => {
    stubProbes({ setup: { status: 404 } });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);
    await waitFor(() => expect(screen.getByTestId("workbench")).toBeTruthy());
  });

  it("completed instance falls through", async () => {
    stubProbes({ setup: { status: 200, value: { completed: "true" } } });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);
    await waitFor(() => expect(screen.getByTestId("workbench")).toBeTruthy());
  });

  it("probe failure fails SAFE — workbench renders", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);
    await waitFor(() => expect(screen.getByTestId("workbench")).toBeTruthy());
  });
});

/** RULING 1 — the admin lands DIRECTLY in the company-setup conversation. Nothing stands in front
 *  of it: no model step, no transcription step, no screen explaining what a company layer is. */
describe("the landing rule — first render after the claim", () => {
  it("a fresh admin is handed off with no step in front of the chat", async () => {
    const calls = stubProbes({ setup: { status: 200, value: {} } });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);

    await waitFor(() => {
      const put = calls.find((c) => c.method === "PUT" && c.url.includes("/api/admin/settings/setup"));
      expect(put).toBeDefined();
      expect(JSON.parse(put!.body || "{}")).toEqual({ global: "handoff" });
    });
    // The screens that used to be in the way.
    expect(screen.queryByText("How should the agent think?")).toBeNull();
    expect(screen.queryByText("Who turns speech into text?")).toBeNull();
    expect(screen.queryByText("Who is this company?")).toBeNull();
    expect(screen.queryByText("Skip for now")).toBeNull();
    // …and the workbench does NOT mount underneath while we are leaving for the conversation.
    expect(screen.queryByTestId("workbench")).toBeNull();
  });

  it("persists BEFORE it navigates", async () => {
    // The navigation destroys this component, so a write started after it may never be sent — and
    // an admin who lands in the setup chat with nothing persisted is thrown back here on the next
    // reload with no sign that the conversation was the actual work.
    const calls = stubProbes({ setup: { status: 200, value: {} } });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    await waitFor(() => expect(reachedNavigate()).toBe(true));
  });
});

/** RULING 3 — handOff must not swallow its own failure. */
describe("a hand-off whose write failed", () => {
  it("does NOT navigate, and says so with a retry", async () => {
    stubProbes({ setup: { status: 200, value: {} }, setupPutFails: true });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);

    await waitFor(() => expect(screen.getByTestId("handoff-failed")).toBeTruthy());
    expect(reachedNavigate()).toBe(false);
    expect(screen.getByText("Try again")).toBeTruthy();
    // and the admin is not silently dropped into a workbench that has no conversation in it
    expect(screen.queryByTestId("workbench")).toBeNull();
  });

  it("a write that answers OK and does not stick stops the loop instead of navigating again", async () => {
    // The exact 2026-09-02 blocker: admin-api dropped the field and answered 200. With an automatic
    // hand-off that is a redirect loop, so coming back in "opening" after we already tried is
    // positive evidence the marker never persisted.
    sessionStorage.setItem(REACHED_NAVIGATE, "1");
    const calls = stubProbes({ setup: { status: 200, value: {} } });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);

    await waitFor(() => expect(screen.getByTestId("handoff-failed")).toBeTruthy());
    // it did not even re-attempt the write, let alone the navigation
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });
});

describe("the corner card", () => {
  it("renders the workbench underneath, states the layer, and withholds the action", async () => {
    stubProbes({
      setup: { status: 200, value: { global: "handoff" } },
      global: { global_setup: "missing", company: null,
        present: ["README.md", "PRINCIPLES.md", "OBJECTIVES.md"],
        missing_files: ["STRUCTURE.md", "MISSING.md"],
        reasons: ["_global is missing STRUCTURE.md."] },
    });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);

    // The whole point of the handoff phase: the chat is live UNDER the card.
    await waitFor(() => expect(screen.getByTestId("workbench")).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId("global-gate-card")).toBeTruthy());

    await waitFor(() => expect(screen.getByText(
      "Company layer: 3 of 5. Who you are, how you work, what you are working toward: written.",
    )).toBeTruthy());
    expect(screen.getByText(/Now: structure — who does what and who can see what\./)).toBeTruthy();
    // read-only until the verifier says yes
    expect(screen.getByText("the agent is writing this with you")).toBeTruthy();
    expect(screen.queryByText("Open this Vexa")).toBeNull();
    // the server's reasons are an ANSWER, not a permanent wall
    expect(screen.queryByText("_global is missing STRUCTURE.md.")).toBeNull();
    // and no filenames anywhere on the card
    expect(screen.getByTestId("global-gate-card").textContent).not.toContain(".md");
  });

  it("offers the action once the SERVER says completed, and only then writes setup.completed", async () => {
    const calls = stubProbes({
      setup: { status: 200, value: { global: "handoff" } },
      global: { global_setup: "completed", company: "Acme GmbH",
        present: ["README.md", "PRINCIPLES.md", "OBJECTIVES.md", "STRUCTURE.md", "MISSING.md"],
        missing_files: [], reasons: [] },
    });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);

    await waitFor(() => expect(screen.getByText("Acme GmbH")).toBeTruthy());
    expect(screen.getByText("Other people can sign in, and mails start going out.")).toBeTruthy();
    // Nothing has claimed setup is finished yet — the human still has to press the button.
    expect(calls.some((c) => c.method === "PUT" && (c.body || "").includes("completed"))).toBe(false);

    fireEvent.click(screen.getByText("Open this Vexa"));
    await waitFor(() => {
      const put = calls.find((c) => c.method === "PUT" && (c.body || "").includes("completed"));
      expect(JSON.parse(put!.body || "{}")).toEqual({ completed: "true" });
    });
    // …and the card is gone, leaving the workbench alone.
    await waitFor(() => expect(screen.queryByTestId("global-gate-card")).toBeNull());
    expect(screen.getByTestId("workbench")).toBeTruthy();
  });

  it("shows the server's reasons ONLY when the verifier refuses after the admin asked to open", async () => {
    const complete = {
      global_setup: "completed", company: "Acme GmbH",
      present: ["README.md", "PRINCIPLES.md", "OBJECTIVES.md", "STRUCTURE.md", "MISSING.md"],
      missing_files: [], reasons: [],
    };
    const refuses = {
      global_setup: "missing", company: null,
      present: ["README.md"], missing_files: ["PRINCIPLES.md"],
      reasons: ["_global is missing PRINCIPLES.md."], gate_sentence: "not ready",
    };
    stubProbes({ setup: { status: 200, value: { global: "handoff" } }, globalSeq: [complete, refuses] });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);

    await waitFor(() => expect(screen.getByText("Open this Vexa")).toBeTruthy());
    fireEvent.click(screen.getByText("Open this Vexa"));

    await waitFor(() => expect(screen.getByText("_global is missing PRINCIPLES.md.")).toBeTruthy());
    // it did NOT declare setup finished on a stale verdict
    expect(screen.getByTestId("global-gate-card")).toBeTruthy();
  });

  it("carries what used to be steps 1 and 2 — one line, and only when something is unset", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      calls.push(u);
      if (u.includes("/api/admin/settings/setup")) {
        if (init?.method === "PUT") return new Response(JSON.stringify({ value: {} }), { status: 200 });
        return new Response(JSON.stringify({ value: { global: "handoff" } }), { status: 200 });
      }
      if (u.includes("/api/global/state")) {
        return new Response(JSON.stringify({
          global_setup: "missing", company: null, present: [],
          missing_files: ["README.md", "PRINCIPLES.md", "OBJECTIVES.md", "STRUCTURE.md", "MISSING.md"], reasons: [],
        }), { status: 200 });
      }
      // models fails, transcription passes
      if (u.includes("/models/test")) return new Response(JSON.stringify({ ok: false, summary: "no creds" }), { status: 200 });
      return new Response(JSON.stringify({ ok: true, summary: "fine" }), { status: 200 });
    }));
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);

    await waitFor(() => expect(screen.getByText(/Not set yet: the agent model\./)).toBeTruthy());
    expect(screen.getByText(/Settings → Models, whenever you like/)).toBeTruthy();
  });
});
