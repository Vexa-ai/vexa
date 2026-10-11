/** Transcription language: the one mapping (spawn fields, summaries, the live-switch body), both
 *  paste-a-link senders carrying the per-meeting override, and Settings loading and saving the
 *  person's default. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../../platform", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useService: () => ({ openTab: vi.fn() }),
}));

import {
  DEFAULT_CHOICE, choiceToSetting, languageSpawnFields, readMeetingLanguage, setMeetingLanguage,
  settingToChoice, summarizeLanguage, LanguageSwitchError, type LanguageChoice,
} from "../transcriptionLanguage";
import { MeetingsOnboarding } from "../meetingsOnboarding";
import { MeetingsList } from "../meeting";
import { TranscriptionLanguageSettings } from "../TranscriptionLanguagePicker";

const choice = (c: Partial<LanguageChoice>): LanguageChoice => ({ ...DEFAULT_CHOICE, ...c });

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("languageSpawnFields — the POST /bots override", () => {
  it("default sends nothing, so the server resolves person → deployment → auto", () => {
    expect(languageSpawnFields(DEFAULT_CHOICE)).toEqual({});
  });
  it("auto forces auto-detect over any default", () => {
    expect(languageSpawnFields(choice({ mode: "auto" }))).toEqual({ language: "auto" });
  });
  it("one language pins it", () => {
    expect(languageSpawnFields(choice({ mode: "one", language: "de" }))).toEqual({ language: "de" });
  });
  it("a few languages send the list, plus the chosen fallback", () => {
    expect(languageSpawnFields(choice({ mode: "few", languages: ["de", "en"], fallback: "de" }))).toEqual({ allowed_languages: ["de", "en"], language: "de" });
    expect(languageSpawnFields(choice({ mode: "few", languages: ["de", "en"] }))).toEqual({ allowed_languages: ["de", "en"] });
  });
});

describe("summaries and round trips", () => {
  it("names the setting in words", () => {
    expect(summarizeLanguage({ language: null, allowed_languages: [] })).toBe("Auto-detect");
    expect(summarizeLanguage({ language: "de", allowed_languages: [] })).toBe("German");
    expect(summarizeLanguage({ language: "de", allowed_languages: ["de", "en"] })).toBe("German or English (falls back to German)");
    expect(summarizeLanguage({ language: null, allowed_languages: ["fr", "de", "en"] })).toBe("French, German or English (falls back to French)");
  });
  it("a setting reads back into the same choice", () => {
    const s = { language: "en", allowed_languages: ["de", "en"] };
    expect(choiceToSetting(settingToChoice(s, "auto"))).toEqual(s);
    expect(settingToChoice({ language: null, allowed_languages: [] }, "default").mode).toBe("default");
  });
  it("a malformed row field reads as codes only", () => {
    expect(readMeetingLanguage({ language: "DE!", allowed_languages: ["de", 3, "english"], source: "user" })).toEqual({ language: null, allowed_languages: ["de"], source: "user" });
    expect(readMeetingLanguage(null)).toBeUndefined();
  });
});

describe("setMeetingLanguage — the live switch", () => {
  it("PUTs the replacing setting; auto-detect is null/null", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ transcription_language: { language: null, allowed_languages: [], source: "meeting" } }), { status: 202 }));
    vi.stubGlobal("fetch", f);
    await setMeetingLanguage("google_meet", "abc-defg-hij", { language: null, allowed_languages: [] });
    expect(f).toHaveBeenCalledWith("/api/bots/google_meet/abc-defg-hij/config", expect.objectContaining({ method: "PUT" }));
    expect(JSON.parse((f.mock.calls[0] as unknown as [string, RequestInit])[1].body as string)).toEqual({ language: null, allowed_languages: null });
  });
  it.each([
    [404, /no bot in this meeting/],
    [409, /isn't listening yet/],
    [503, /couldn't be confirmed/],
  ])("maps %i to words that name the fix", async (status, text) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "x" }), { status })));
    const err = await setMeetingLanguage("zoom", "1", { language: "de", allowed_languages: [] }).catch((e) => e);
    expect(err).toBeInstanceOf(LanguageSwitchError);
    expect(err.message).toMatch(text);
  });
  it("a 422 carries the server's reason", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "fallback must be in the list" }), { status: 422 })));
    const err = await setMeetingLanguage("zoom", "1", { language: "de", allowed_languages: [] }).catch((e) => e);
    expect(err.message).toContain("fallback must be in the list");
  });
});

/** A fetch stub that records POST /api/bots bodies and answers everything else harmlessly. */
function stubBots() {
  const posts: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u === "/api/bots" && init?.method === "POST") {
      posts.push(JSON.parse(init.body as string));
      return new Response(JSON.stringify({ id: 1 }), { status: 201 });
    }
    if (u.includes("/api/user/calendars")) return new Response(JSON.stringify({ calendars: [] }), { status: 200 });
    if (u.includes("/api/meetings")) return new Response(JSON.stringify({ meetings: [] }), { status: 200 });
    return new Response("{}", { status: 200 });
  }));
  return posts;
}

const LINK = "https://meet.google.com/abc-defg-hij";

describe.each([
  ["first-run card (DropBotInline)", () => render(<MeetingsOnboarding variant="slim" />), "Send bot"],
  ["meetings rail (MeetingsList.addBot)", () => render(<MeetingsList />), "Add bot"],
])("%s", (_name, mount, sendLabel) => {
  it("sends no language fields by default", async () => {
    const posts = stubBots();
    mount();
    fireEvent.change(screen.getByPlaceholderText(/Paste a meeting link/), { target: { value: LINK } });
    expect((screen.getByLabelText("Language") as HTMLSelectElement).value).toBe("default");
    fireEvent.click(screen.getByRole("button", { name: sendLabel }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ platform: "google_meet", native_meeting_id: "abc-defg-hij" });
    expect(posts[0]).not.toHaveProperty("language");
    expect(posts[0]).not.toHaveProperty("allowed_languages");
  });

  it("carries a per-meeting override in the POST body", async () => {
    const posts = stubBots();
    mount();
    fireEvent.change(screen.getByPlaceholderText(/Paste a meeting link/), { target: { value: LINK } });
    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "one" } });
    fireEvent.change(screen.getByLabelText("Transcribe in"), { target: { value: "de" } });
    fireEvent.click(screen.getByRole("button", { name: sendLabel }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ native_meeting_id: "abc-defg-hij", language: "de" });
  });

  it("a few languages: list plus fallback", async () => {
    const posts = stubBots();
    mount();
    fireEvent.change(screen.getByPlaceholderText(/Paste a meeting link/), { target: { value: LINK } });
    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "few" } });
    fireEvent.change(screen.getByLabelText("Add a language"), { target: { value: "de" } });
    fireEvent.change(screen.getByLabelText("Add a language"), { target: { value: "en" } });
    fireEvent.change(screen.getByLabelText("When none of these is detected, use"), { target: { value: "de" } });
    fireEvent.click(screen.getByRole("button", { name: sendLabel }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ allowed_languages: ["de", "en"], language: "de" });
  });
});

describe("Settings — the default transcription language", () => {
  function stubPrefs(put: (body: Record<string, unknown>) => Response) {
    const puts: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url) === "/api/user/transcription" && init?.method === "PUT") {
        const body = JSON.parse(init.body as string);
        puts.push(body);
        return put(body);
      }
      return new Response(JSON.stringify({ url: null, token_set: false, language: "de", allowed_languages: [] }), { status: 200 });
    }));
    return puts;
  }

  it("loads the saved default and saves {language, allowed_languages}", async () => {
    const puts = stubPrefs((b) => new Response(JSON.stringify({ ...b, language: b.language || null }), { status: 200 }));
    render(<TranscriptionLanguageSettings />);
    await waitFor(() => expect((screen.getByLabelText("Transcribe in") as HTMLSelectElement).value).toBe("de"));
    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "few" } });
    fireEvent.change(screen.getByLabelText("Add a language"), { target: { value: "en" } });
    fireEvent.click(screen.getByRole("button", { name: "Save language" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ language: "", allowed_languages: ["de", "en"] });
    await screen.findByText(/Saved/);
  });

  it("clearing back to the deployment default sends \"\" and []", async () => {
    const puts = stubPrefs(() => new Response(JSON.stringify({ language: null, allowed_languages: [] }), { status: 200 }));
    render(<TranscriptionLanguageSettings />);
    await waitFor(() => expect((screen.getByLabelText("Language") as HTMLSelectElement).value).toBe("one"));
    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "default" } });
    fireEvent.click(screen.getByRole("button", { name: "Save language" }));
    await waitFor(() => expect(puts).toEqual([{ language: "", allowed_languages: [] }]));
  });

  it("shows the 422 detail beside the control and keeps the choice", async () => {
    stubPrefs(() => new Response(JSON.stringify({ detail: "language must be one of allowed_languages" }), { status: 422 }));
    render(<TranscriptionLanguageSettings />);
    await waitFor(() => expect((screen.getByLabelText("Transcribe in") as HTMLSelectElement).value).toBe("de"));
    fireEvent.change(screen.getByLabelText("Transcribe in"), { target: { value: "fr" } });
    fireEvent.click(screen.getByRole("button", { name: "Save language" }));
    expect((await screen.findByRole("alert")).textContent).toContain("language must be one of allowed_languages");
    expect((screen.getByLabelText("Transcribe in") as HTMLSelectElement).value).toBe("fr");
  });
});
