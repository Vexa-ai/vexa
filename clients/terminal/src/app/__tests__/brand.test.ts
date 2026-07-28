/** brand — the white-label switch.
 *
 *  Two properties matter and neither is obvious from reading the module: an UNSET build must be
 *  byte-identical to the shipped product (otherwise the switch is a redesign nobody asked for),
 *  and the values are emitted verbatim into a <style> block, so a malformed one must be dropped
 *  rather than allowed to close the declaration and take every rule after it down with it.
 */
import { describe, expect, it, vi, afterEach } from "vitest";

const BRAND_ENV = [
  "NEXT_PUBLIC_BRAND_NAME", "NEXT_PUBLIC_BRAND_ACCENT", "NEXT_PUBLIC_BRAND_ACCENT_LIGHT",
  "NEXT_PUBLIC_BRAND_ON_ACCENT", "NEXT_PUBLIC_BRAND_FONT", "NEXT_PUBLIC_BRAND_LOGO_URL",
] as const;

/** Re-import the module with a given env — BRAND is a module constant, so it binds at load. */
async function brandWith(env: Partial<Record<(typeof BRAND_ENV)[number], string>>) {
  for (const k of BRAND_ENV) delete process.env[k];
  Object.assign(process.env, env);
  vi.resetModules();
  return (await import("../brand")).BRAND;
}

afterEach(() => { for (const k of BRAND_ENV) delete process.env[k]; });

describe("brand", () => {
  it("an unconfigured build is the shipped product, unchanged", async () => {
    const b = await brandWith({});
    expect(b.name).toBe("Vexa");
    expect(b.logo).toBe("/vexa-logo.svg");
    // empty ⇒ layout emits NO override rule at all, so globals.css keeps its own defaults
    expect(b.accent).toBe("");
    expect(b.accentLight).toBe("");
    expect(b.onAccent).toBe("");
    expect(b.font).toBe("");
  });

  it("carries the values a deployment sets", async () => {
    const b = await brandWith({
      NEXT_PUBLIC_BRAND_NAME: "Dark Alpha Capital",
      NEXT_PUBLIC_BRAND_ACCENT: "#40a2e3",
      NEXT_PUBLIC_BRAND_ACCENT_LIGHT: "#1b7bbb",
      NEXT_PUBLIC_BRAND_FONT: '"Helvetica Neue", Helvetica, Arial, sans-serif',
      NEXT_PUBLIC_BRAND_LOGO_URL: "/dac-logo.svg",
    });
    expect(b.name).toBe("Dark Alpha Capital");
    expect(b.accent).toBe("#40a2e3");
    expect(b.accentLight).toBe("#1b7bbb");
    expect(b.font).toBe('"Helvetica Neue", Helvetica, Arial, sans-serif');
    expect(b.logo).toBe("/dac-logo.svg");
  });

  it("accepts the CSS colour forms a brand actually uses", async () => {
    for (const c of ["#fff", "#40a2e3", "rgb(64, 162, 227)", "hsl(204 74% 57%)", "oklch(70% .14 240)"]) {
      expect((await brandWith({ NEXT_PUBLIC_BRAND_ACCENT: c })).accent).toBe(c);
    }
  });

  it("DROPS a value that could escape the style declaration", async () => {
    // `;}` would end the rule and let everything after it apply globally; `<` could end the tag.
    for (const bad of ["red;}html{display:none", "red</style><script>x()</script>", "a".repeat(65)]) {
      expect((await brandWith({ NEXT_PUBLIC_BRAND_ACCENT: bad })).accent).toBe("");
    }
    for (const bad of ["Helvetica;}html{display:none", "x".repeat(201)]) {
      expect((await brandWith({ NEXT_PUBLIC_BRAND_FONT: bad })).font).toBe("");
    }
  });

  it("treats whitespace-only as unset rather than as a value", async () => {
    const b = await brandWith({ NEXT_PUBLIC_BRAND_NAME: "   ", NEXT_PUBLIC_BRAND_ACCENT: "  " });
    expect(b.name).toBe("Vexa");
    expect(b.accent).toBe("");
  });
});
