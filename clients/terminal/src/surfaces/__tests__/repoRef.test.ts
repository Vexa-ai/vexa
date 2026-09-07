/** The 2026-09-02 incident, client-side: a GitHub PAT was pasted into the attach dialog's Repository
 *  field and SENT. The point of these is not that a nicer error is shown — it is that the value never
 *  leaves the tab, and that nothing which does leave carries a secret. */
import { describe, expect, it } from "vitest";
import { checkRepo, looksLikeToken, SHAPE_SENTENCE, TOKEN_SENTENCE } from "../repoRef";
import { MASK, redactSecrets } from "../redactSecrets";

const PAT = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8";
const FINE = "github_pat_11ABCDE0aAbBcCdDeEfF_gGhHiIjJkKlLmMnNoOpPqQrRsStTuUvVwWxXyYzZ01";

describe("checkRepo — the whitelist", () => {
  it.each([
    ["https://github.com/acme/kg", "https://github.com/acme/kg.git"],
    ["https://github.com/acme/kg.git", "https://github.com/acme/kg.git"],
    ["git@github.com:acme/kg.git", "git@github.com:acme/kg.git"],
    ["git@github.com:acme/kg", "git@github.com:acme/kg.git"],
    ["ssh://git@github.com/acme/kg.git", "ssh://git@github.com/acme/kg.git"],
    ["acme/kg", "https://github.com/acme/kg.git"],
    ["  acme/kg.git ", "https://github.com/acme/kg.git"],
  ])("accepts %s", (raw, url) => {
    const r = checkRepo(raw);
    expect(r).toEqual({ ok: true, url });
  });

  it.each([PAT, FINE, "glpat-aBcDeFgHiJkLmNoPqRsT"])("refuses the token %s by name", (t) => {
    const r = checkRepo(t);
    expect(r).toEqual({ ok: false, kind: "token", sentence: TOKEN_SENTENCE });
  });

  it("refuses a URL that carries a credential, and keeps ssh://git@ working", () => {
    expect(checkRepo(`https://${PAT}@github.com/acme/kg.git`)).toMatchObject({ kind: "token" });
    expect(checkRepo("https://someone:hunter2@github.com/acme/kg.git")).toMatchObject({ kind: "token" });
    expect(checkRepo("ssh://git@github.com/acme/kg.git")).toMatchObject({ ok: true });
  });

  it.each(["/workspaces/u_someone_else", "file:///etc", "not a url", "https://github.com/acme", "acme"])(
    "refuses %s as a shape", (bad) => {
      expect(checkRepo(bad)).toEqual({ ok: false, kind: "shape", sentence: SHAPE_SENTENCE });
    });

  it("never echoes the value back in its refusal", () => {
    expect(JSON.stringify(checkRepo(PAT))).not.toContain(PAT);
  });

  it("looksLikeToken is narrow enough to be useful", () => {
    expect(looksLikeToken(PAT)).toBe(true);
    for (const ok of ["git@github.com:acme/kg.git", "acme/kg", "main", ""]) {
      expect(looksLikeToken(ok)).toBe(false);
    }
  });
});

describe("redactSecrets — nothing that reaches a person or the console carries one", () => {
  it("masks the exact string that leaked", () => {
    const out = redactSecrets(`fatal: repository '${PAT}' does not exist`);
    expect(out).not.toContain(PAT);
    expect(out).toContain(MASK);
  });

  it("masks a URL credential and an unknown long secret", () => {
    expect(redactSecrets("https://someone:hunter2@github.com/x/y.git")).not.toContain("hunter2");
    const unknown = "zzQ7rT9wL2mK4nP6vB8xC1dF3gH5jK7lM9nO0p";
    expect(redactSecrets(`remote: ${unknown}`)).not.toContain(unknown);
  });

  it("leaves a git object id alone — diagnostics are the reason the string is shown at all", () => {
    const sha = "a".repeat(40);
    expect(redactSecrets(`fatal: bad object ${sha}`)).toContain(sha);
  });

  it("leaves an ssh public key alone — it is the ANSWER, not a secret", () => {
    const key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHqZ0mQfZ5xW1kTn2vJ8sQpYbR3cL7dM4eF6gH9iJ0kL vexa-workspace-ws-x";
    expect(redactSecrets(`add this key:\n${key}`)).toContain("ssh-ed25519 ");
  });

  it("is idempotent and survives odd input", () => {
    const once = redactSecrets(`a ${PAT} b`);
    expect(redactSecrets(once)).toBe(once);
    expect(redactSecrets(null)).toBe("");
    expect(redactSecrets(undefined)).toBe("");
  });
});

/** The answer must survive the scrubbing — else the fix breaks the feature it exists to protect.
 *  `PLAIN_FP`'s base64 body deliberately contains neither `+` nor `/`: those characters fall outside
 *  the generic rule's class and split a long run, so a fingerprint containing one survives by
 *  accident. Roughly one in four does not. This is the one that does not. */
describe("public key material survives, credentials do not", () => {
  const PLAIN_FP = "SHA256:" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5nP7rT9v";
  const KEY =
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHqZ0mQfZ5xW1kTn2vJ8sQpYbR3cL7dM4eF6gH9iJ0kL vexa-workspace-ws-acme";
  const PAT = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8";

  it("renders the whole deploy-key card unmasked", () => {
    const card = `no credential yet (${PLAIN_FP}). Add this public key, then say \`done\` when added:\n${KEY}`;
    const out = redactSecrets(card);
    expect(out).toContain(KEY);
    expect(out).toContain(PLAIN_FP);
    expect(out).toContain("say `done` when added");
  });

  it("still masks a PAT sitting in the same message — the allow-list is not a hole", () => {
    const out = redactSecrets(`could not read Username for 'https://${PAT}@github.com'\n${KEY}\n${PLAIN_FP}`);
    expect(out).not.toContain(PAT);
    expect(out).toContain(MASK);
    expect(out).toContain(KEY);
    expect(out).toContain(PLAIN_FP);
  });

  it("keeps an MD5 fingerprint too", () => {
    const md5 = "MD5:" + new Array(16).fill("ab").join(":");
    expect(redactSecrets(`key fingerprint ${md5}`)).toContain(md5);
  });
});
