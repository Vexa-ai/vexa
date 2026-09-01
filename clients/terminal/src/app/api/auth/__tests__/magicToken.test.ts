/** Magic-link tokens — the signed emailed door.
 *
 *  These are the four properties the whole scheme rests on: only WE can mint one (signature), a
 *  stale one stops working (expiry), a link works exactly ONCE (jti ledger), and the `next=` a
 *  link carries can never point off-site (open-redirect guard). Everything else in the flow is
 *  plumbing around them.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_TTL_SECONDS,
  _resetJtiLedger,
  consumeJti,
  mintMagicToken,
  redeemMagicToken,
  safeNext,
  ttlSeconds,
  verifyMagicToken,
} from "../magicToken";

beforeEach(() => {
  _resetJtiLedger();
  vi.stubEnv("NEXTAUTH_SECRET", "test-signing-secret");
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
    vi.stubEnv("NEXTAUTH_SECRET", "a-completely-different-secret");
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

  it("fails CLOSED with no NEXTAUTH_SECRET — nothing can be minted or verified", () => {
    const minted = mintMagicToken("someone@example.com");
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;
    vi.stubEnv("NEXTAUTH_SECRET", "");
    expect(mintMagicToken("someone@example.com").ok).toBe(false);
    expect(verifyMagicToken(minted.token)).toEqual({ ok: false, reason: "unconfigured" });
  });
});

describe("single use (the jti ledger)", () => {
  it("redeems once, then refuses the same link as used", () => {
    const minted = mintMagicToken("someone@example.com");
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;
    expect(redeemMagicToken(minted.token)).toMatchObject({ ok: true, email: "someone@example.com" });
    expect(redeemMagicToken(minted.token)).toEqual({ ok: false, reason: "used" });
    expect(redeemMagicToken(minted.token)).toEqual({ ok: false, reason: "used" });
  });

  it("two DIFFERENT links are independent (the ledger keys on jti, not on the address)", () => {
    const a = mintMagicToken("someone@example.com");
    const b = mintMagicToken("someone@example.com");
    expect(a.ok && b.ok).toBe(true);
    if (!a.ok || !b.ok) return;
    expect(a.jti).not.toBe(b.jti);
    expect(redeemMagicToken(a.token).ok).toBe(true);
    expect(redeemMagicToken(b.token).ok).toBe(true);
  });

  it("verifyMagicToken does NOT burn the jti — only redeem does", () => {
    const minted = mintMagicToken("someone@example.com");
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;
    expect(verifyMagicToken(minted.token).ok).toBe(true);
    expect(verifyMagicToken(minted.token).ok).toBe(true);
    expect(redeemMagicToken(minted.token).ok).toBe(true);
  });

  it("forgets a jti once its token could no longer verify anyway (the ledger is bounded)", () => {
    const now = 1_700_000_000_000;
    const expiresAt = Math.floor(now / 1000) + 60;
    expect(consumeJti("jti-1", expiresAt, now)).toBe(true);
    expect(consumeJti("jti-1", expiresAt, now)).toBe(false);
    // Past the expiry the sweep drops it — harmless, because verification refuses it first.
    expect(consumeJti("jti-1", expiresAt, now + 120_000)).toBe(true);
  });
});

describe("safeNext — the open-redirect guard", () => {
  it("keeps site-relative paths, including the deeplink query the mail carries", () => {
    expect(safeNext("/")).toBe("/");
    expect(safeNext("/?ask=catch-up")).toBe("/?ask=catch-up");
    expect(safeNext("/?meeting=google_meet/abc-defg-hij&view=readme")).toBe("/?meeting=google_meet/abc-defg-hij&view=readme");
    expect(safeNext("/minutes#section")).toBe("/minutes#section");
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
