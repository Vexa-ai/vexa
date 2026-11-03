/** One identity system (UI decisions 15f, 15g): a deterministic monogram and colour for everything
 *  that has a face — the same answer for the same id on every load. */
import { describe, expect, it } from "vitest";
import { IDENTITY_HUES, IDENTITY_SHADES, agentMark, hashId, identityColour, identitySrc, monogram } from "../identity";

describe("identityColour", () => {
  it("same id → same colour, every time", () => {
    const a = identityColour("k4m9x2q7bd");
    for (let i = 0; i < 50; i++) expect(identityColour("k4m9x2q7bd")).toEqual(a);
  });
  it("is one of the curated hues, so it reads in both themes", () => {
    for (const id of ["a", "b", "studio-c1", "u_jane", "", "Ünïcødé"]) {
      expect(IDENTITY_HUES).toContain(identityColour(id).hue);
    }
  });
  it("spreads different ids over the palette", () => {
    const seen = new Set(Array.from({ length: 200 }, (_, i) => identityColour(`ws-${i}`).index));
    expect(seen.size).toBeGreaterThanOrEqual(IDENTITY_HUES.length - 1);
  });
  it("hashId is stable and unsigned", () => {
    expect(hashId("abc")).toBe(hashId("abc"));
    expect(hashId("abc")).toBeGreaterThanOrEqual(0);
    expect(hashId("abc")).not.toBe(hashId("abd"));
  });
});

describe("monogram", () => {
  it.each([
    ["Jane Smith", "JS"],
    ["jane", "J"],
    ["Studio — spring launch", "SS"],
    ["ann-marie o'neil", "AO"],
    ["jane.smith@example.com", "JS"],
    ["Élodie Ørsted", "ÉØ"],
    ["  ", "?"],
    ["", "?"],
    ["42 things", "4T"],
  ])("%s → %s", (name, mono) => expect(monogram(name)).toBe(mono));
  it("same name → same monogram", () => expect(monogram("Acme Widgets")).toBe(monogram("Acme Widgets")));
});

describe("agentMark", () => {
  it("is deterministic per id", () => {
    expect(agentMark("routine:abc")).toEqual(agentMark("routine:abc"));
    const shapes = new Set(Array.from({ length: 60 }, (_, i) => agentMark(`r${i}`).shape));
    expect(shapes.size).toBeGreaterThan(2);
  });
});

describe("identitySrc", () => {
  it("a workspace's image comes from its own authed route", () => {
    expect(identitySrc({ kind: "workspace", slug: "studio c1" })).toBe("/api/workspaces/studio%20c1/identity/icon");
  });
  it("a company's logo comes through the media proxy", () => {
    expect(identitySrc({ kind: "company", logo: "d:acme.example.com" })).toBe("/api/home/media/d%3Aacme.example.com");
  });
  it("a CRM person who is not a user never shows a face", () => {
    expect(identitySrc({ kind: "person", user: false, src: "/x.png", avatar: "g:0123" })).toBeUndefined();
    expect(identitySrc({ kind: "person", user: true, src: "/api/people/u1/photo" })).toBe("/api/people/u1/photo");
    expect(identitySrc({ kind: "person", user: true, avatar: "g:0123" })).toBe("/api/home/media/g%3A0123");
  });
  it("an explicit src wins for agents and workspaces", () => {
    expect(identitySrc({ kind: "agent", src: "/a.svg" })).toBe("/a.svg");
    expect(identitySrc({ kind: "agent" })).toBeUndefined();
  });
});

describe("contrast — every hue reads in both themes", () => {
  const rgb = (h: number, s: number, l: number) => {
    s /= 100; l /= 100;
    const k = (n: number) => (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    return [f(0), f(8), f(4)];
  };
  const lum = (c: number[]) => {
    const [r, g, b] = c.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  for (const theme of ["dark", "light"] as const) {
    it(`${theme}: monogram text ≥ 4.5:1 on its background for all hues`, () => {
      const { bg, fg } = IDENTITY_SHADES[theme];
      for (const h of IDENTITY_HUES) {
        const a = lum(rgb(h, bg[0], bg[1])), b = lum(rgb(h, fg[0], fg[1]));
        const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        expect(ratio, `hue ${h}`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }
});
