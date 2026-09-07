/** GATE 13 (R-E12) — the minutes seed route no longer splices caller text into a shell.
 *
 *  What it did: `echo '${f.b64}' | base64 -d > ${dest}` inside `sh -c`, with `f.b64` validated
 *  nowhere. One apostrophe in the body closed the quote and the rest ran as root inside the
 *  agent-api container. The route has no identity check at all — it gates on NODE_ENV and the
 *  terminal mode — so the only thing bounding it was that the server is a developer's laptop.
 *
 *  Two properties are asserted, and they are separate defences: the payload is validated against
 *  the base64 alphabet, and it travels on STDIN, so no validation bug can reach argv either way.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ runs: [] as { container: string; script: string; args: string[]; stdin: string }[] }));

vi.mock("../minutes/seed/dockerSh", () => ({
  dockerSh: async (container: string, script: string, args: string[], stdin: string) => {
    h.runs.push({ container, script, args, stdin });
  },
}));

import { POST } from "../minutes/seed/route";

function req(body: unknown) {
  return { json: async () => body } as unknown as Parameters<typeof POST>[0];
}

describe("minutes seed route", () => {
  beforeEach(() => {
    h.runs.length = 0;
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("NEXT_PUBLIC_TERMINAL_MODE", "minutes");
  });
  afterEach(() => vi.unstubAllEnvs());

  it("refuses a payload that is not base64", async () => {
    const hostile = "aGk='; id > /tmp/pwned; echo '";
    const res = await POST(req({ wsId: "room1", files: [{ path: "a.md", b64: hostile }] }));

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "bad b64" });
    expect(h.runs).toHaveLength(0);
  });

  it("passes the payload on stdin and every path as an argv parameter", async () => {
    const b64 = Buffer.from("# hello", "utf-8").toString("base64");
    const res = await POST(req({ wsId: "room1", files: [{ path: "notes/a.md", b64 }] }));

    expect(res.status).toBe(200);
    expect(h.runs).toHaveLength(1);
    const { script, args, stdin } = h.runs[0];

    expect(stdin).toBe(b64);
    // Nothing caller-controlled is inside the script text — that is where the injection lived.
    expect(script).not.toContain(b64);
    expect(script).not.toContain("notes/a.md");
    expect(script).not.toContain("room1");
    expect(args).toEqual(["/workspaces/room1", "notes/a.md"]);
  });

  it("still refuses a traversing path", async () => {
    const b64 = Buffer.from("x", "utf-8").toString("base64");
    const res = await POST(req({ wsId: "room1", files: [{ path: "../../etc/x", b64 }] }));
    expect(res.status).toBe(400);
    expect(h.runs).toHaveLength(0);
  });

  it("sends the README on stdin too, and 404s outside a development minutes build", async () => {
    const ok = await POST(req({ wsId: "room1", name: "Room One" }));
    expect(ok.status).toBe(200);
    expect(h.runs).toHaveLength(1);
    expect(h.runs[0].script).not.toContain(h.runs[0].stdin);
    expect(h.runs[0].args).toEqual(["/workspaces/room1"]);

    vi.stubEnv("NODE_ENV", "production");
    const gone = await POST(req({ wsId: "room1" }));
    expect(gone.status).toBe(404);
    expect(h.runs).toHaveLength(1);
  });
});
