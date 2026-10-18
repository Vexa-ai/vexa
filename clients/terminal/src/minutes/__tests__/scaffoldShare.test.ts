/** R-A08 — the transcript share is redeemed against the scaffold id, never read off the URL.
 *
 *  The mailed link used to be `/?s=<id>&tshare=<share token>`: a bearer credential in a query
 *  string, crossing a public hostname, the recipient's mail provider, every proxy between, and
 *  whoever they forward it to. `worker/engine.py` states the opposite rule one file away for the
 *  MCP delegation token; the weaker spelling sat on the more exposed artefact.
 *
 *  The client half is one function. It is here rather than inline in `App.tsx` for the reason the
 *  module header gives: `scaffold.ts` is the ONLY place that knows the wire shape, so reconciling
 *  with the server is an edit to one file.
 */
import { describe, expect, it, vi } from "vitest";
import { redeemScaffoldShare } from "../scaffold";

const ok = (body: unknown) =>
  vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })) as unknown as typeof fetch;

describe("redeemScaffoldShare", () => {
  it("posts to the scaffold's own id and returns the token", async () => {
    const f = ok({ token: "97.tok-for-priya" });
    expect(await redeemScaffoldShare("abc123", f)).toBe("97.tok-for-priya");
    expect((f as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0])
      .toBe("/api/scaffolds/abc123/share");
    expect((f as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1].method).toBe("POST");
  });

  it("returns null when the scaffold carries no share — the ordinary case, not a failure", async () => {
    expect(await redeemScaffoldShare("abc123", ok({ token: null }))).toBeNull();
  });

  it("never throws: a refusal, a dead service and a body that is not JSON are all null", async () => {
    const refused = vi.fn(async () => new Response("{}", { status: 404 })) as unknown as typeof fetch;
    const dead = vi.fn(async () => { throw new Error("network"); }) as unknown as typeof fetch;
    const junk = vi.fn(async () => new Response("<html>", { status: 200 })) as unknown as typeof fetch;
    expect(await redeemScaffoldShare("abc123", refused)).toBeNull();
    expect(await redeemScaffoldShare("abc123", dead)).toBeNull();
    expect(await redeemScaffoldShare("abc123", junk)).toBeNull();
  });

  it("refuses an id that is not one, without calling the network", async () => {
    const f = ok({ token: "x" });
    expect(await redeemScaffoldShare("../../etc/passwd", f)).toBeNull();
    expect((f as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
  });

  it("returns null for a token the server did not send as a string", async () => {
    expect(await redeemScaffoldShare("abc123", ok({ token: 42 }))).toBeNull();
  });
});
