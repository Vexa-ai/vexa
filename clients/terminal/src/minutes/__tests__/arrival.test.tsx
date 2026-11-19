/** THE ARRIVAL — every `?s=` link ends in a rendered state, in the document it landed in.
 *
 *  The defect these pin (2026-09-05, dogfood): the writer cleaned the URL with
 *  `location.replace(pathname)` — a navigation — while the reader, whose effects React runs FIRST,
 *  already had `GET /api/scaffolds/<id>` in flight and had taken the id out of storage. The request
 *  was aborted, the second document found nothing pending, and both outcomes — the chat AND the
 *  refusal card — were lost to the same reload. A real first-time invitee saw an empty "New chat".
 *
 *  So the three things below are one thing said three ways: the URL is cleaned WITHOUT navigating,
 *  the id survives until the fetch ANSWERS, and neither effect order can drop an arrival.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { useEffect, type ReactNode } from "react";
import {
  PENDING_SCAFFOLD, beginArrival, cleanArrivalParam, pendingArrival, resolveArrival, useScaffoldArrival,
} from "../arrival";
import type { Scaffold, ScaffoldRefusal } from "../scaffold";

const ID = "SC-abc_123";
const WIRE = {
  id: ID, kind: "invite-offer", meeting: "97", native: "abc-defg-hij", phase: "post",
  workspaces: ["_global", "u_priya"],
  refs: { title: "Show B Lighting dailies", participants: ["priya@acme.test"] },
  opening_preset: "minutes-review-invite",
  opening_text: "[minutes-review] Someone clicked through about 97 …",
  tabs: ["meeting:note"], focus: "meeting:note",
};

const res = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const answering = (status: number, body: unknown) =>
  vi.fn(async (...args: unknown[]) => { void args; return res(status, body); });

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("the URL is cleaned IN PLACE — no second document", () => {
  it("takes `?s=` off the address bar through history, not through a navigation", () => {
    window.history.replaceState({}, "", `/?s=${ID}`);
    const spy = vi.spyOn(window.history, "replaceState");
    beginArrival(ID);
    // Positive evidence, not an absence: jsdom implements no navigation at all, so a
    // `location.replace(pathname)` would have left the address bar still reading `?s=…`. A clean
    // search here can only have come from the replaceState this asserts.
    expect(spy).toHaveBeenCalledTimes(1);
    expect(window.location.search).toBe("");
    expect(window.location.pathname).toBe("/");
  });

  it("removes only `s` — the other links clean themselves by reloading, on purpose", () => {
    window.history.replaceState({}, "", `/?s=${ID}&invite=tok&meeting=google_meet%2Fabc`);
    beginArrival(ID);
    const q = new URLSearchParams(window.location.search);
    expect(q.get("s")).toBeNull();
    expect(q.get("invite")).toBe("tok");
    expect(q.get("meeting")).toBe("google_meet/abc");
  });

  it("stashes the id — the link carries an id, and the id travels by storage", () => {
    window.history.replaceState({}, "", `/?s=${ID}`);
    beginArrival(ID);
    expect(localStorage.getItem(PENDING_SCAFFOLD)).toBe(ID);
  });

  it("touches nothing on a URL that carries no arrival", () => {
    window.history.replaceState({}, "", "/somewhere?x=1");
    const spy = vi.spyOn(window.history, "replaceState");
    cleanArrivalParam();
    expect(spy).not.toHaveBeenCalled();
    expect(window.location.search).toBe("?x=1");
  });
});

describe("the id leaves storage on the ANSWER, never on the read", () => {
  it("is still pending while the fetch is in flight", async () => {
    beginArrival(ID);
    let release: (r: Response) => void = () => {};
    const inflight = new Promise<Response>((r) => { release = r; });
    const settled = resolveArrival(ID, (() => inflight) as unknown as typeof fetch);
    expect(pendingArrival()).toBe(ID);          // reading it did not consume it
    release(res(200, WIRE));
    await settled;
    expect(pendingArrival()).toBeNull();
  });

  it("clears on a scaffold that opened, and on every FINAL refusal", async () => {
    const finals: [number, unknown][] = [
      [200, WIRE],                              // opened
      [404, { detail: "no such scaffold" }],    // not the recipient
      [403, { detail: "not yours" }],           // someone else's
      [200, { id: ID }],                        // a body that is not a scaffold
    ];
    for (const [status, body] of finals) {
      beginArrival(ID);
      await resolveArrival(ID, answering(status, body) as unknown as typeof fetch);
      expect(pendingArrival()).toBeNull();
    }
  });

  it("KEEPS it when the service could not be reached — that card promises a reload will open it", async () => {
    beginArrival(ID);
    const got = await resolveArrival(ID, (async () => { throw new Error("offline"); }) as unknown as typeof fetch);
    expect(got.ok).toBe(false);
    if (!got.ok) expect(got.refusal.reason).toBe("unavailable");
    expect(pendingArrival()).toBe(ID);
  });
});

/** The real tree's shape: the component that reads the URL is the PARENT of the one that owns the
 *  chat list, and React runs a child's effects before its parent's. That is not a detail of these
 *  tests — it is the ordering the defect lived in. */
function Writer({ id, children }: { id: string; children: ReactNode }) {
  useEffect(() => { beginArrival(id); }, [id]);
  return <>{children}</>;
}
function Reader({ onOpen, onRefuse }: {
  onOpen: (s: Scaffold) => void; onRefuse: (r: ScaffoldRefusal) => void;
}) {
  useScaffoldArrival({ onOpen, onRefuse });
  return <div>reader</div>;
}

describe("an arrival ends in one of the two states, in either effect order", () => {
  it("the recipient gets the chat — though the reader's effect ran BEFORE the writer's", async () => {
    const f = answering(200, WIRE);
    vi.stubGlobal("fetch", f);
    window.history.replaceState({}, "", `/?s=${ID}`);
    const onOpen = vi.fn(), onRefuse = vi.fn();
    render(<Writer id={ID}><Reader onOpen={onOpen} onRefuse={onRefuse} /></Writer>);
    await waitFor(() => expect(onOpen).toHaveBeenCalledTimes(1));
    expect(onOpen.mock.calls[0][0].id).toBe(ID);
    expect(onRefuse).not.toHaveBeenCalled();
    expect(f.mock.calls[0][0]).toBe(`/api/scaffolds/${ID}`);
    expect(window.location.search).toBe("");    // and the URL was cleaned around it
  });

  it("…and when the stash landed first, which is the other order", async () => {
    vi.stubGlobal("fetch", answering(200, WIRE));
    window.history.replaceState({}, "", `/?s=${ID}`);
    beginArrival(ID);
    const onOpen = vi.fn(), onRefuse = vi.fn();
    render(<Reader onOpen={onOpen} onRefuse={onRefuse} />);
    await waitFor(() => expect(onOpen).toHaveBeenCalledTimes(1));
    expect(onRefuse).not.toHaveBeenCalled();
  });

  it("someone who is not the recipient gets the refusal, not an empty chat", async () => {
    vi.stubGlobal("fetch", answering(404, { detail: "no such scaffold" }));
    window.history.replaceState({}, "", `/?s=${ID}`);
    const onOpen = vi.fn(), onRefuse = vi.fn();
    render(<Writer id={ID}><Reader onOpen={onOpen} onRefuse={onRefuse} /></Writer>);
    await waitFor(() => expect(onRefuse).toHaveBeenCalledTimes(1));
    expect(onRefuse.mock.calls[0][0].reason).toBe("not-found");
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("…in the other order too", async () => {
    vi.stubGlobal("fetch", answering(404, { detail: "no such scaffold" }));
    beginArrival(ID);
    const onOpen = vi.fn(), onRefuse = vi.fn();
    render(<Reader onOpen={onOpen} onRefuse={onRefuse} />);
    await waitFor(() => expect(onRefuse).toHaveBeenCalledTimes(1));
    expect(onRefuse.mock.calls[0][0].reason).toBe("not-found");
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("an id that is not one, and a service that will not answer, are both stated too", async () => {
    vi.stubGlobal("fetch", answering(200, WIRE));
    beginArrival("../../etc/passwd");
    const bad = { onOpen: vi.fn(), onRefuse: vi.fn() };
    render(<Reader {...bad} />);
    await waitFor(() => expect(bad.onRefuse).toHaveBeenCalledTimes(1));
    expect(bad.onRefuse.mock.calls[0][0].reason).toBe("malformed");
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    beginArrival(ID);
    const dead = { onOpen: vi.fn(), onRefuse: vi.fn() };
    render(<Reader {...dead} />);
    await waitFor(() => expect(dead.onRefuse).toHaveBeenCalledTimes(1));
    expect(dead.onRefuse.mock.calls[0][0].reason).toBe("unavailable");
    expect(pendingArrival()).toBe(ID);          // still there for the reload the card offers
  });

  it("asks once, though both the stash-check and the announcement could have started it", async () => {
    const f = answering(200, WIRE);
    vi.stubGlobal("fetch", f);
    beginArrival(ID);                            // already stashed BEFORE the reader mounts…
    const onOpen = vi.fn(), onRefuse = vi.fn();
    // …and the writer announces again once it does.
    render(<Writer id={ID}><Reader onOpen={onOpen} onRefuse={onRefuse} /></Writer>);
    await waitFor(() => expect(onOpen).toHaveBeenCalledTimes(1));
    expect(f).toHaveBeenCalledTimes(1);
  });
});
