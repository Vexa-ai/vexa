import { afterEach, describe, expect, it, vi } from "vitest";

async function fresh() {
  vi.resetModules();
  return import("../accountMode");
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("accountMode — identity.v1 account_mode, read once from /api/auth/me", () => {
  it("is null until read, then what the server said", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (u: string) => { calls.push(u); return new Response(JSON.stringify({ account_mode: "claude" })); }));
    const m = await fresh();
    expect(m.accountMode()).toBeNull();
    await Promise.all([m.loadAccountMode(), m.loadAccountMode()]);
    expect(m.accountMode()).toBe("claude");
    await m.loadAccountMode();
    expect(calls).toEqual(["/api/auth/me"]);
  });

  it("an unknown or degraded answer stays null (the standard terminal)", async () => {
    for (const body of [{ account_mode: null }, { account_mode: "CLAUDE" }, {}]) {
      vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body))));
      const m = await fresh();
      await m.loadAccountMode();
      expect(m.accountMode()).toBeNull();
    }
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("down"); }));
    const m = await fresh();
    await m.loadAccountMode();
    expect(m.accountMode()).toBeNull();
  });

  it("the test seam answers without a fetch", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const m = await fresh();
    m.setAccountModeOverride("claude");
    expect(m.accountMode()).toBe("claude");
    await m.loadAccountMode();
    expect(f).not.toHaveBeenCalled();
    m.setAccountModeOverride(null);
  });
});
