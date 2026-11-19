// @vitest-environment node
/** The signing secrets: what the terminal refuses to start with, and the key an emailed link uses.
 *
 *  A secret is usable only when it is set, at least 32 bytes, and not a value published in the
 *  repository. The emailed link is never signed with the session secret itself.
 */
import { spawnSync } from "node:child_process";
import { createHmac } from "node:crypto";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  MIN_SECRET_BYTES,
  PUBLISHED_SECRETS,
  authSecretStartupError,
  magicLinkKey,
  secretProblem,
} from "../authSecret.mjs";

const STRONG = "9f1c2e7a4b6d8f0a1c3e5a7b9d1f3a5c7e9b1d3f5a7c9e1b3d5f7a9c1e3b5d7f";
const OTHER_STRONG = "0a2c4e6f8b1d3f5a7c9e0b2d4f6a8c1e3b5d7f9a0c2e4b6d8f1a3c5e7b9d0f2a";
const TERMINAL_DIR = fileURLToPath(new URL("../../../../../", import.meta.url));

describe("which secrets are usable", () => {
  it("refuses an empty or blank secret", () => {
    for (const v of [undefined, "", "   "]) expect(secretProblem(v)).toMatch(/not set/);
  });

  it("refuses every value published in the repository, whatever its case or padding", () => {
    expect(PUBLISHED_SECRETS).toContain("dev-nextauth-secret");
    expect(PUBLISHED_SECRETS).toContain("vexa-lite-nextauth-secret");
    for (const v of PUBLISHED_SECRETS) {
      expect(secretProblem(v)).not.toBeNull();
      expect(secretProblem(`  ${v.toUpperCase()}  `)).not.toBeNull();
    }
  });

  it("refuses a secret shorter than 32 bytes and accepts one of 32", () => {
    expect(MIN_SECRET_BYTES).toBe(32);
    expect(secretProblem("x".repeat(31))).toMatch(/shorter than 32 bytes/);
    expect(secretProblem("x".repeat(32))).toBeNull();
    expect(secretProblem(STRONG)).toBeNull();
  });
});

describe("startup", () => {
  it("names the problem and the fix", () => {
    expect(authSecretStartupError({})).toMatch(/NEXTAUTH_SECRET is not set.*openssl rand -hex 32/);
    expect(authSecretStartupError({ NEXTAUTH_SECRET: "dev-nextauth-secret" })).toMatch(/published/);
    expect(authSecretStartupError({ NEXTAUTH_SECRET: STRONG })).toBeNull();
  });

  it("holds a configured MAGIC_LINK_SECRET to the same rule, and apart from the session secret", () => {
    expect(authSecretStartupError({ NEXTAUTH_SECRET: STRONG, MAGIC_LINK_SECRET: "short" })).toMatch(/MAGIC_LINK_SECRET/);
    expect(authSecretStartupError({ NEXTAUTH_SECRET: STRONG, MAGIC_LINK_SECRET: STRONG })).toMatch(/must differ/);
    expect(authSecretStartupError({ NEXTAUTH_SECRET: STRONG, MAGIC_LINK_SECRET: OTHER_STRONG })).toBeNull();
  });

  it.each([
    ["unset", ""],
    ["a published value", "dev-nextauth-secret"],
    ["a short value", "too-short-to-sign-anything"],
  ])("the server refuses to start with NEXTAUTH_SECRET %s", (_label, value) => {
    const run = spawnSync(process.execPath, ["server.mjs"], {
      cwd: TERMINAL_DIR,
      env: { PATH: process.env.PATH, NODE_ENV: "production", PORT: "0", NEXTAUTH_SECRET: value },
      encoding: "utf8",
      timeout: 60_000,
    });
    expect(run.status).toBe(1);
    expect(run.stderr).toMatch(/refusing to start: NEXTAUTH_SECRET/);
  }, 70_000);
});

describe("the emailed link's key", () => {
  it("is derived from the session secret and is never the session secret", () => {
    const key = magicLinkKey({ NEXTAUTH_SECRET: STRONG });
    expect(key).toBeTruthy();
    expect(key).not.toBe(STRONG);
    expect(key).toBe(magicLinkKey({ NEXTAUTH_SECRET: STRONG }));
    expect(key).not.toBe(magicLinkKey({ NEXTAUTH_SECRET: OTHER_STRONG }));
    expect(key).toBe(createHmac("sha256", STRONG).update("vexa-terminal/magic-link/v1").digest("hex"));
  });

  it("is MAGIC_LINK_SECRET when one is configured", () => {
    expect(magicLinkKey({ NEXTAUTH_SECRET: STRONG, MAGIC_LINK_SECRET: OTHER_STRONG })).toBe(OTHER_STRONG);
  });

  it("does not exist for an unusable secret", () => {
    for (const env of [{}, { NEXTAUTH_SECRET: "dev-nextauth-secret" }, { NEXTAUTH_SECRET: "short" },
                       { NEXTAUTH_SECRET: STRONG, MAGIC_LINK_SECRET: "short" },
                       { NEXTAUTH_SECRET: STRONG, MAGIC_LINK_SECRET: STRONG }]) {
      expect(magicLinkKey(env)).toBeNull();
    }
  });
});
