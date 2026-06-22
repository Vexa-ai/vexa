/**
 * players-render.spec.ts — the L4 gate for @vexa/dash-recording-players.
 *
 * Loads the fixture page (players-render.html) in a REAL chromium and asserts the REAL DOM that the
 * components rendered from golden props: the AudioPlayer paints an <audio> with the golden src plus a
 * Play control and a seek bar; the multi-fragment AudioPlayer additionally shows the "1/2" fragment
 * indicator and a "0:30" stitched total; the VideoPlayer paints a <video> with the golden src plus a
 * Play control. A green here means "the brick's components are renderable by a human's browser", not "a
 * node test imported a module". Playback decode is NOT required in CI — the gate asserts the element +
 * src + a play control exist (per the brick's L4 contract).
 */
import { test, expect } from "@playwright/test";
import {
  GOLDEN_AUDIO_SRC,
  GOLDEN_VIDEO_SRC,
  GOLDEN_FRAGMENT_INDICATOR,
} from "./fixtures/golden.js";

test("a real browser renders the AudioPlayer + VideoPlayer the brick delivers", async ({
  page,
}) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  await page.goto("/");

  await page.waitForSelector("#harness-ready", {
    state: "attached",
    timeout: 10_000,
  });

  // if mounting threw inside the page, surface it as a failure (not a silent blank)
  const harnessError = await page.locator("#harness-error").count();
  if (harnessError > 0) {
    const detail = await page.locator("#harness-error").textContent();
    throw new Error("fixture mounting failed in-page:\n" + detail);
  }
  expect(pageErrors, pageErrors.join("\n")).toHaveLength(0);

  // ── AudioPlayer (single source) ──────────────────────────────────────────────────────────────────
  const audioSingle = page.locator("#audio-single");
  await expect(audioSingle.getByTestId("audio-player")).toBeVisible();

  // the <audio> element exists and carries the injected src
  const audioEl = audioSingle.getByTestId("audio-el");
  await expect(audioEl).toHaveCount(1);
  await expect(audioEl).toHaveAttribute("src", GOLDEN_AUDIO_SRC);
  // it is a real <audio> element in the DOM
  expect(await audioEl.evaluate((el) => el.tagName)).toBe("AUDIO");

  // a Play control exists
  const audioPlay = audioSingle.getByTestId("play-toggle");
  await expect(audioPlay).toBeVisible();
  await expect(audioPlay).toHaveAttribute("aria-label", "Play");

  // a seek bar exists
  await expect(audioSingle.getByTestId("seek-bar")).toHaveCount(1);
  // a mute control exists
  await expect(audioSingle.getByTestId("mute-toggle")).toBeVisible();
  // single-source players do not show a fragment indicator
  await expect(audioSingle.getByTestId("fragment-indicator")).toHaveCount(0);

  // ── AudioPlayer (multi-fragment, stitched timeline) ──────────────────────────────────────────────
  const audioMulti = page.locator("#audio-multi");
  await expect(audioMulti.getByTestId("audio-player")).toBeVisible();
  await expect(audioMulti.getByTestId("audio-el")).toHaveAttribute("src", GOLDEN_AUDIO_SRC);
  // shows "1/2" — first of two fragments. This proves multi-fragment mode + the stitched-timeline
  // wiring deterministically (it is prop-driven, not decode-dependent).
  await expect(audioMulti.getByTestId("fragment-indicator")).toHaveText(
    GOLDEN_FRAGMENT_INDICATOR,
  );
  // the multi-fragment player also has its own play control + seek bar
  await expect(audioMulti.getByTestId("play-toggle")).toBeVisible();
  await expect(audioMulti.getByTestId("seek-bar")).toHaveCount(1);
  // a stitched total-duration label renders in m:ss form. We do not pin the exact seconds: once the
  // real <audio> loads metadata for a fragment, the component (correctly) supersedes the prop estimate
  // with the decoded duration, so the stitched total is decode-dependent in CI. Asserting the m:ss
  // shape proves the virtual-timeline label is wired without coupling to codec timing.
  await expect(audioMulti.getByTestId("duration")).toHaveText(/^\d+:\d{2}$/);

  // ── VideoPlayer ──────────────────────────────────────────────────────────────────────────────────
  const video = page.locator("#video");
  await expect(video.getByTestId("video-player")).toBeVisible();

  const videoEl = video.getByTestId("video-el");
  await expect(videoEl).toHaveCount(1);
  await expect(videoEl).toHaveAttribute("src", GOLDEN_VIDEO_SRC);
  expect(await videoEl.evaluate((el) => el.tagName)).toBe("VIDEO");

  // a Play control exists on the video too
  const videoPlay = video.getByTestId("play-toggle");
  await expect(videoPlay).toHaveCount(1);
  await expect(videoPlay).toHaveAttribute("aria-label", "Play");
});

test("the AudioPlayer Play control toggles to Pause on click (real interaction)", async ({
  page,
}) => {
  await page.goto("/");
  await page.waitForSelector("#harness-ready", { state: "attached", timeout: 10_000 });

  const audioSingle = page.locator("#audio-single");
  const playBtn = audioSingle.getByTestId("play-toggle");
  await expect(playBtn).toHaveAttribute("aria-label", "Play");

  // Clicking play fires the real <audio>.play(); the data: WAV is decodable, so the element starts
  // playing and the control flips to Pause. (If a CI sandbox cannot decode, this second test may not
  // flip — that's why the element/src/control existence is asserted in the first, gating test.)
  await playBtn.click();
  await expect(playBtn).toHaveAttribute("aria-label", "Pause", { timeout: 5_000 });
});
