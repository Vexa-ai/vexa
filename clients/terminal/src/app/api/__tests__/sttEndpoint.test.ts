import { describe, expect, it } from "vitest";
import { STT_PATH, sttEndpoint } from "../stt/endpoint";

/** #511 C4 — one URL contract across all four consumers. TRANSCRIPTION_SERVICE_URL is accepted as a
 *  bare base OR as the full endpoint; the dictation route used to append blindly, so a full-path URL
 *  that transcribes meetings fine 404'd here. */
describe("sttEndpoint — the shared TRANSCRIPTION_SERVICE_URL rule", () => {
  it("appends the transcriptions path to a bare base", () => {
    expect(sttEndpoint("https://api.openai.com")).toBe(`https://api.openai.com${STT_PATH}`);
  });

  it("tolerates trailing slashes and surrounding whitespace", () => {
    expect(sttEndpoint("https://api.openai.com///")).toBe(`https://api.openai.com${STT_PATH}`);
    expect(sttEndpoint("  https://api.openai.com  ")).toBe(`https://api.openai.com${STT_PATH}`);
  });

  it("does NOT double-path a URL that already carries the endpoint", () => {
    const full = `https://api.openai.com${STT_PATH}`;
    expect(sttEndpoint(full)).toBe(full);
    expect(sttEndpoint(`${full}/`)).toBe(full);
  });

  it("does NOT double-v1 a base that already ends with /v1", () => {
    // Groq's documented base is https://api.groq.com/openai/v1 — an operator naturally
    // includes /v1. Appending /v1/audio/transcriptions would produce /v1/v1/... → 404.
    expect(sttEndpoint("https://api.groq.com/openai/v1")).toBe(`https://api.groq.com/openai/v1/audio/transcriptions`);
  });

  it("returns empty for an unset value so the route can answer 503", () => {
    expect(sttEndpoint("")).toBe("");
    expect(sttEndpoint("   ")).toBe("");
  });

  it("leaves a self-hosted path prefix intact", () => {
    // a reverse-proxied deployment (…/stt) is a base like any other — append, do not rewrite
    expect(sttEndpoint("https://internal.example/stt")).toBe(`https://internal.example/stt${STT_PATH}`);
  });
});
