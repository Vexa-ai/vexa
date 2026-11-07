/** Settings → Appearance (founder 2026-10-15: pick an accent colour, "a normal font pick", in the
 *  left bottom), and a pasted meeting link is never a chat's or a meeting's name. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ACCENTS, appearanceCss, contrast, deriveAccent, normalizeAppearance, PAGE } from "../../platform/appearance";
import { SettingsDialog } from "../SettingsDialog";
import { displayLabel, isPlaceholderLabel, titleFromTurn } from "../chats";
import { linkCallName, meetingDisplayName } from "../callsList";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("accent contrast", () => {
  it("every curated accent reads as it stands, in both themes", () => {
    for (const a of ACCENTS) for (const t of ["dark", "light"] as const) {
      const d = deriveAccent(a[t], t);
      expect(contrast(d.accent, d.on), `${a.id} ${t}`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(d.text, PAGE[t]), `${a.id} ${t} text`).toBeGreaterThanOrEqual(4.5);
    }
    expect(deriveAccent("#d8855c", "dark").accent).toBe("#d8855c");    // the current accent is untouched
  });
  it("a custom colour that would not read is moved until it does, and says so", () => {
    const y = deriveAccent("#ffee00", "light");
    expect(y.adjusted).toBe(true);
    expect(contrast(y.accent, y.on)).toBeGreaterThanOrEqual(4.5);
    const n = deriveAccent("#101060", "dark");
    expect(contrast(n.text, PAGE.dark)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(n.accent, n.on)).toBeGreaterThanOrEqual(4.5);
  });
  it("applies through the tokens only, and unknown values fall back", () => {
    const css = appearanceCss(normalizeAppearance({ accent: "teal", font_ui: "inter", font_reading: "source-serif", density: "compact" }));
    expect(css).toMatch(/--accent: #[0-9a-f]{6};/);
    expect(css).toContain("--font-sans: \"Inter\"");
    expect(css).toContain("--font-reading: \"Source Serif 4\"");
    expect(css).toContain("--space-2: 6px");
    expect(normalizeAppearance({ accent: "red;}", font_ui: "Comic Sans" })).toMatchObject({ accent: "clay", font_ui: "geist" });
  });
});

describe("Settings → Appearance", () => {
  it("a swatch applies at once and saves for the person; a font is a plain dropdown", async () => {
    const puts: unknown[] = [];
    let user: Record<string, string> = {};
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PUT") { const b = JSON.parse(String(init.body)); puts.push(b); user = { ...user, ...b }; }
      return new Response(JSON.stringify({ user, company: { accent: "clay" }, effective: { accent: "clay", ...user } }), { status: 200 });
    }));
    render(<SettingsDialog onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("radio", { name: "Blue" }));
    expect(document.getElementById("vx-appearance")?.textContent).toContain(`--accent: ${deriveAccent(ACCENTS[1].dark, "dark").accent}`);
    fireEvent.change(screen.getByLabelText("Interface font"), { target: { value: "plex" } });
    await waitFor(() => expect(puts).toEqual([{ accent: "blue" }, { font_ui: "plex" }]));
    expect(document.getElementById("vx-appearance")?.textContent).toContain("IBM Plex Sans");
  });
});

describe("a pasted link is not a name", () => {
  const url = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%22Tid%22";
  it("the chat and the meeting are named as the call, never the address", () => {
    const at = Date.parse("2026-10-15T15:30:00");
    expect(linkCallName(url, at)).toMatch(/^Teams call · \w{3} 15:30$/);
    expect(displayLabel({ label: url, when: at })).toMatch(/^Teams call · /);
    expect(displayLabel({ label: url.slice(0, 47) + "…", when: at })).toMatch(/^Teams call/);
    expect(isPlaceholderLabel(url)).toBe(true);
    expect(titleFromTurn(url)).toBe("");
    expect(meetingDisplayName({ id: 7, platform: "ms_teams", title: url, start_time: "2026-10-15T15:30:00" } as never, { chatName: url }))
      .toMatch(/^Teams call · \w{3} 15:30$/);
  });
});
