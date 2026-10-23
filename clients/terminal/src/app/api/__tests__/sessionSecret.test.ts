// @vitest-environment node
/** The session secret at boot: a production terminal without a usable VEXA_TERMINAL_SESSION_SECRET
 *  refuses to start, naming the key; `next dev` keeps the ordinary-key fallback. */
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { sessionSecretStartupError } from "../sessionSecret.mjs";

const STRONG = "9f1c2e7a4b6d8f0a1c3e5a7b9d1f3a5c7e9b1d3f5a7c9e1b3d5f7a9c1e3b5d7f";
const SESSION = "4b6d8f0a1c3e5a7b9d1f3a5c7e9b1d3f5a7c9e1b3d5f7a9c1e3b5d7f9f1c2e7a";
const TERMINAL_DIR = fileURLToPath(new URL("../../../../", import.meta.url));

describe("the session secret at boot", () => {
  it("refuses an unset, blank, short or published secret in production, never echoing it", () => {
    for (const v of [undefined, "", "  ", "x".repeat(31), "changeme"]) {
      const err = sessionSecretStartupError({ NODE_ENV: "production", VEXA_TERMINAL_SESSION_SECRET: v });
      expect(err).toMatch(/^VEXA_TERMINAL_SESSION_SECRET /);
      if (v && v.trim()) expect(err).not.toContain(v);
    }
  });

  it("accepts a usable secret in production", () => {
    expect(sessionSecretStartupError({ NODE_ENV: "production", VEXA_TERMINAL_SESSION_SECRET: SESSION })).toBeNull();
  });

  it("does not refuse outside production (next dev keeps the ordinary-key fallback)", () => {
    expect(sessionSecretStartupError({})).toBeNull();
    expect(sessionSecretStartupError({ NODE_ENV: "development" })).toBeNull();
  });

  it("the server refuses to start in production without it", () => {
    const run = spawnSync(process.execPath, ["server.mjs"], {
      cwd: TERMINAL_DIR,
      env: { PATH: process.env.PATH, NODE_ENV: "production", PORT: "0", NEXTAUTH_SECRET: STRONG },
      encoding: "utf8",
      timeout: 60_000,
    });
    expect(run.status).toBe(1);
    expect(run.stderr).toMatch(/refusing to start: VEXA_TERMINAL_SESSION_SECRET is not set/);
  }, 70_000);
});
