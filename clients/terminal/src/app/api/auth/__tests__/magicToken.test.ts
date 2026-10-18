/** Magic-link tokens — the signed emailed door.
 *
 *  These are the four properties the whole scheme rests on: only WE can mint one (signature), a
 *  stale one stops working (expiry), a link works exactly ONCE (admin-api's shared record of used jtis), and the `next=` a
 *  link carries can never point off-site (open-redirect guard). Everything else in the flow is
 *  plumbing around them.
 */
import { createHmac } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("../adminApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../adminApi")>()),
  // admin-api's single-use record for links, held in memory (./linkLedgerDouble.ts)
  redeemSigninLink: (jti: string, expiresAt: number) => linkLedger.redeem(jti, expiresAt),
}));
import {
  DEFAULT_TTL_SECONDS,
  MAX_TTL_SECONDS,
  mintMagicToken,
  redeemMagicToken,
  safeNext,
  ttlSeconds,
  verifyMagicToken,
} from "../magicToken";
import { linkLedger } from "./linkLedgerDouble";

function b64url(buf: Buffer): string {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

beforeEach(() => {
  linkLedger.reset();
  vi.stubEnv("MAGIC_LINK_SECRET", "");
  vi.stubEnv("NEXTAUTH_SECRET", "test-signing-secret-0123456789abcdef");
  vi.stubEnv("MAGIC_LINK_TTL_SECONDS", "");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("mint / verify", () => {
  it("round-trips the email and defaults to a 15-minute TTL", () => {
    const now = 1_700_000_000_000;
    const minted = mintMagicToken("someone@example.com", { now });
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;
    expect(minted.expiresAt).toBe(Math.floor(now / 1000) + DEFAULT_TTL_SECONDS);
    expect(ttlSeconds()).toBe(900);

    const v = verifyMagicToken(minted.token, { now });
    expect(v).toMatchObject({ ok: true, email: "someone@example.com", jti: minted.jti });
  });

  it("refuses a token whose payload was edited (the signature is over the payload)", () => {
    const minted = mintMagicToken("victim@example.com");
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;
    const [, sig] = minted.token.split(".");
    const forgedPayload = Buffer.from(
      JSON.stringify({ e: "attacker@evil.example", x: Math.floor(Date.now() / 1000) + 600, j: "x" }),
      "utf8",
    )
      .toString("base64")
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    expect(verifyMagicToken(`${forgedPayload}.${sig}`)).toEqual({ ok: false, reason: "bad-signature" });
  });

  it("refuses a token signed with a different secret", () => {
    const minted = mintMagicToken("someone@example.com");
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;
    vi.stubEnv("NEXTAUTH_SECRET", "a-completely-different-secret-0123456789");
    expect(verifyMagicToken(minted.token)).toEqual({ ok: false, reason: "bad-signature" });
  });

  it("refuses garbage and structurally wrong tokens", () => {
    for (const junk of ["", "not-a-token", "a.b.c", "onlyonepart", ".", "abc."]) {
      const v = verifyMagicToken(junk);
      expect(v.ok).toBe(false);
      if (!v.ok) expect(["malformed", "bad-signature"]).toContain(v.reason);
    }
  });

  it("expires: valid one second before, refused one second after", () => {
    const now = 1_700_000_000_000;
    const minted = mintMagicToken("someone@example.com", { now, ttl: 900 });
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;
    expect(verifyMagicToken(minted.token, { now: now + 899_000 }).ok).toBe(true);
    expect(verifyMagicToken(minted.token, { now: now + 901_000 })).toEqual({ ok: false, reason: "expired" });
  });

  it("MAGIC_LINK_TTL_SECONDS overrides the default", () => {
    vi.stubEnv("MAGIC_LINK_TTL_SECONDS", "60");
    expect(ttlSeconds()).toBe(60);
    const now = 1_700_000_000_000;
    const minted = mintMagicToken("someone@example.com", { now });
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;
    expect(minted.expiresAt).toBe(Math.floor(now / 1000) + 60);
  });

  it("a token signed with the session secret itself is refused — links use their own key", () => {
    const payload = b64url(Buffer.from(JSON.stringify({ e: "someone@example.com", x: Math.floor(Date.now() / 1000) + 600, j: "j1" })));
    const sig = b64url(createHmac("sha256", "test-signing-secret-0123456789abcdef").update(payload).digest());
    expect(verifyMagicToken(`${payload}.${sig}`)).toEqual({ ok: false, reason: "bad-signature" });
  });

  it.each(["dev-nextauth-secret", "vexa-lite-nextauth-secret", "short-secret"])(
    "a published or short session secret (%s) closes the door: nothing is minted or verified",
    (weak) => {
      vi.stubEnv("NEXTAUTH_SECRET", weak);
      expect(mintMagicToken("someone@example.com").ok).toBe(false);
      const payload = b64url(Buffer.from(JSON.stringify({ e: "someone@example.com", x: Math.floor(Date.now() / 1000) + 600, j: "j1" })));
      for (const key of [weak, createHmac("sha256", weak).update("vexa-terminal/magic-link/v1").digest("hex")]) {
        const sig = b64url(createHmac("sha256", key).update(payload).digest());
        expect(verifyMagicToken(`${payload}.${sig}`)).toEqual({ ok: false, reason: "unconfigured" });
      }
    },
  );

  it("a configured MAGIC_LINK_SECRET signs the links", () => {
    vi.stubEnv("MAGIC_LINK_SECRET", "a-separate-link-secret-0123456789abcdef");
    const minted = mintMagicToken("someone@example.com");
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;
    expect(verifyMagicToken(minted.token).ok).toBe(true);
    vi.stubEnv("MAGIC_LINK_SECRET", "");
    expect(verifyMagicToken(minted.token)).toEqual({ ok: false, reason: "bad-signature" });
  });

  it("caps the lifetime: the TTL setting is clamped and a far-future expiry is refused", () => {
    vi.stubEnv("MAGIC_LINK_TTL_SECONDS", String(30 * 24 * 3600));
    expect(ttlSeconds()).toBe(MAX_TTL_SECONDS);
    const now = 1_700_000_000_000;
    const long = mintMagicToken("someone@example.com", { now, ttl: 30 * 24 * 3600 });
    expect(long.ok).toBe(true);
    if (!long.ok) return;
    expect(long.expiresAt).toBe(Math.floor(now / 1000) + MAX_TTL_SECONDS);

    const key = createHmac("sha256", "test-signing-secret-0123456789abcdef").update("vexa-terminal/magic-link/v1").digest("hex");
    const payload = b64url(Buffer.from(JSON.stringify({ e: "someone@example.com", x: Math.floor(now / 1000) + 2 * 24 * 3600, j: "j2" })));
    const sig = b64url(createHmac("sha256", key).update(payload).digest());
    expect(verifyMagicToken(`${payload}.${sig}`, { now })).toEqual({ ok: false, reason: "malformed" });
  });

  it("fails CLOSED with no NEXTAUTH_SECRET — nothing can be minted or verified", () => {
    const minted = mintMagicToken("someone@example.com");
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;
    vi.stubEnv("NEXTAUTH_SECRET", "");
    expect(mintMagicToken("someone@example.com").ok).toBe(false);
    expect(verifyMagicToken(minted.token)).toEqual({ ok: false, reason: "unconfigured" });
  });
});

describe("single use (admin-api's shared record)", () => {
  it("redeems once, then refuses the same link as used", async () => {
    const minted = mintMagicToken("someone@example.com");
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;
    expect(await redeemMagicToken(minted.token)).toMatchObject({ ok: true, email: "someone@example.com" });
    expect(await redeemMagicToken(minted.token)).toEqual({ ok: false, reason: "used" });
    expect(await redeemMagicToken(minted.token)).toEqual({ ok: false, reason: "used" });
  });

  it("two DIFFERENT links are independent (the record keys on jti, not on the address)", async () => {
    const a = mintMagicToken("someone@example.com");
    const b = mintMagicToken("someone@example.com");
    expect(a.ok && b.ok).toBe(true);
    if (!a.ok || !b.ok) return;
    expect(a.jti).not.toBe(b.jti);
    expect((await redeemMagicToken(a.token)).ok).toBe(true);
    expect((await redeemMagicToken(b.token)).ok).toBe(true);
  });

  it("verifyMagicToken does NOT burn the jti — only redeem does", async () => {
    const minted = mintMagicToken("someone@example.com");
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;
    expect(verifyMagicToken(minted.token).ok).toBe(true);
    expect(verifyMagicToken(minted.token).ok).toBe(true);
    expect((await redeemMagicToken(minted.token)).ok).toBe(true);
  });

  it("a link redeemed by ANOTHER process is used here too", async () => {
    const minted = mintMagicToken("someone@example.com");
    if (!minted.ok) throw new Error("mint failed");
    await linkLedger.redeem(minted.jti, minted.expiresAt);
    expect(await redeemMagicToken(minted.token)).toEqual({ ok: false, reason: "used" });
  });

  it("an unreachable record refuses, and does not spend the link", async () => {
    const minted = mintMagicToken("someone@example.com");
    if (!minted.ok) throw new Error("mint failed");
    linkLedger.down = true;
    expect(await redeemMagicToken(minted.token)).toEqual({ ok: false, reason: "unavailable" });
    linkLedger.down = false;
    expect((await redeemMagicToken(minted.token)).ok).toBe(true);
  });

  it("a link that does not verify never reaches the record", async () => {
    linkLedger.down = true;                     // would answer "unavailable" if it were asked
    expect(await redeemMagicToken("garbage")).toEqual({ ok: false, reason: "malformed" });
  });
});

describe("safeNext — the open-redirect guard", () => {
  it("keeps site-relative paths, including the deeplink query the mail carries", () => {
    expect(safeNext("/")).toBe("/");
    expect(safeNext("/?ask=catch-up")).toBe("/?ask=catch-up");
    expect(safeNext("/?meeting=google_meet/abc-defg-hij&view=readme")).toBe("/?meeting=google_meet/abc-defg-hij&view=readme");
    expect(safeNext("/minutes#section")).toBe("/minutes#section");
  });

  it("keeps the CANONICAL WORKSPACE URL a mail carries (PRD decision 26.2)", () => {
    // `/w/<workspace-id>/<path>` is the one link that works in mail, in chat and inside another
    // workspace's document — so it has to survive the sign-in redeem, which is where a link that
    // authenticates AND composes would otherwise lose its destination.
    expect(safeNext("/w/k4m5x2q7bd/kg/entities/person/nora-quill.md"))
      .toBe("/w/k4m5x2q7bd/kg/entities/person/nora-quill.md");
    expect(safeNext("/w/k4m5x2q7bd")).toBe("/w/k4m5x2q7bd");
    expect(safeNext("/w/k4m5x2q7bd/kg/a%20b.md")).toBe("/w/k4m5x2q7bd/kg/a%20b.md");
  });

  it("refuses anything that could leave this origin", () => {
    for (const hostile of [
      "https://evil.example/steal",
      "http://evil.example",
      "//evil.example",
      "//evil.example/path",
      "/\\evil.example",
      "\\\\evil.example",
      "javascript:alert(1)",
      "data:text/html,<script>",
      "mailto:someone@example.com",
      "evil.example",
      "/%2f%2fevil.example",
      "/%5c%5cevil.example",
    ]) {
      expect(safeNext(hostile)).toBe("/");
    }
  });

  it("refuses control characters, bad encoding, and non-strings", () => {
    expect(safeNext("/ok\nLocation: https://evil.example")).toBe("/");
    expect(safeNext("/bad%zz")).toBe("/");
    expect(safeNext(null)).toBe("/");
    expect(safeNext(undefined)).toBe("/");
    expect(safeNext("")).toBe("/");
    expect(safeNext("   ")).toBe("/");
  });

  it("honours a caller-supplied fallback", () => {
    expect(safeNext("https://evil.example", "/home")).toBe("/home");
  });
});
