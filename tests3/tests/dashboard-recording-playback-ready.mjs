import { chromium } from "playwright";

const dashboardUrl = process.env.DASHBOARD_URL || "http://localhost:3001";
const email = process.env.DASHBOARD_TEST_EMAIL || "test@vexa.ai";
const configuredMeetingId = process.env.DASHBOARD_RECORDING_MEETING_ID || process.env.DASHBOARD_MEETING_ID || "";

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
});

try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const failures = [];

  page.on("response", async (response) => {
    const url = response.url();
    if (
      response.status() >= 400 ||
      url.includes("/api/vexa/recordings/") ||
      url.includes("/api/vexa/transcripts/") ||
      url.includes("localhost:9100/") ||
      url.includes("/vexa/recordings/")
    ) {
      let body = "";
      try {
        body = (await response.text()).replace(/\s+/g, " ").slice(0, 300);
      } catch {
        body = "";
      }
      failures.push(`${response.status()} ${url} ${body}`);
    }
  });

  await page.goto(`${dashboardUrl}/login`, { waitUntil: "domcontentloaded", timeout: 30000 });
  const login = await page.evaluate(async (loginEmail) => {
    const response = await fetch("/api/auth/send-magic-link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ email: loginEmail }),
    });
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  }, email);

  if (!login.ok || !login.body?.success) {
    throw new Error(`direct login failed: HTTP ${login.status} ${JSON.stringify(login.body).slice(0, 300)}`);
  }

  let meetingId = configuredMeetingId;
  if (!meetingId) {
    const candidate = await page.evaluate(async () => {
      for (let offset = 0; offset <= 300; offset += 50) {
        const response = await fetch(`/api/vexa/meetings?limit=50&offset=${offset}`, { credentials: "include" });
        const body = await response.json().catch(() => ({}));
        const meetings = Array.isArray(body.meetings) ? body.meetings : [];
        if (meetings.length === 0) break;
        for (const meeting of meetings) {
          if (meeting.status !== "completed") continue;
          const detailResponse = await fetch(`/api/vexa/meetings/${meeting.id}`, { credentials: "include" });
          if (!detailResponse.ok) continue;
          const detail = await detailResponse.json().catch(() => ({}));
          if (detail.status !== "completed") continue;
          const recordings = Array.isArray(detail.data?.recordings) ? detail.data.recordings : [];
          if (recordings.some((recording) => recording?.playback_url?.audio)) {
            const platform = detail.platform;
            const nativeId = detail.native_meeting_id;
            if (!platform || !nativeId) continue;
            const transcriptResponse = await fetch(
              `/api/vexa/transcripts/${encodeURIComponent(platform)}/${encodeURIComponent(nativeId)}?meeting_id=${encodeURIComponent(detail.id || meeting.id)}`,
              { credentials: "include" }
            );
            if (!transcriptResponse.ok) continue;
            const transcript = await transcriptResponse.json().catch(() => ({}));
            const segments = Array.isArray(transcript.segments) ? transcript.segments : [];
            const visibleText = segments
              .map((segment) => String(segment?.text || "").replace(/\s+/g, " ").trim())
              .find((text) => text.length >= 12);
            if (!visibleText) continue;
            return detail.id || meeting.id || "";
          }
        }
      }
      return "";
    });
    meetingId = String(candidate || "");
  }

  if (!meetingId) {
    throw new Error("no completed meeting with recording.playback_url.audio found for dashboard playback probe");
  }

  await page.goto(`${dashboardUrl}/meetings/${meetingId}`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(3000);

  const bodyText = await page.locator("body").innerText({ timeout: 10000 });
  const normalized = bodyText.replace(/\s+/g, " ");
  if (normalized.includes("Recording is processing")) {
    throw new Error(`completed recording still renders processing state for meeting ${meetingId}: ${normalized.slice(0, 500)}`);
  }
  if (normalized.includes("Recording was enabled, but no finalized recording artifact is available yet")) {
    throw new Error(`completed recording renders missing artifact state for meeting ${meetingId}: ${normalized.slice(0, 500)}`);
  }
  if (normalized.includes("Connection error loading recording")) {
    throw new Error(`completed recording renders connection error for meeting ${meetingId}: ${normalized.slice(0, 500)}`);
  }
  if (normalized.includes("Preparing audio")) {
    throw new Error(`completed recording still renders Preparing audio spinner for meeting ${meetingId}: ${normalized.slice(0, 500)}`);
  }

  const transcriptProbe = await page.evaluate(async (id) => {
    const meetingResponse = await fetch(`/api/vexa/meetings/${id}`, { credentials: "include" });
    const meeting = await meetingResponse.json().catch(() => ({}));
    const platform = meeting.platform;
    const nativeId = meeting.native_meeting_id;
    if (!meetingResponse.ok || !platform || !nativeId) {
      return {
        ok: false,
        status: meetingResponse.status,
        error: `meeting detail missing platform/native id: ${JSON.stringify(meeting).slice(0, 300)}`,
        segments: [],
      };
    }
    const transcriptResponse = await fetch(
      `/api/vexa/transcripts/${encodeURIComponent(platform)}/${encodeURIComponent(nativeId)}?meeting_id=${encodeURIComponent(id)}`,
      { credentials: "include" }
    );
    const transcript = await transcriptResponse.json().catch(() => ({}));
    return {
      ok: transcriptResponse.ok,
      status: transcriptResponse.status,
      error: transcript.detail || transcript.error || "",
      segments: Array.isArray(transcript.segments) ? transcript.segments : [],
    };
  }, meetingId);

  if (!transcriptProbe.ok) {
    throw new Error(`dashboard transcript route failed for meeting ${meetingId}: HTTP ${transcriptProbe.status} ${transcriptProbe.error}`);
  }
  if (transcriptProbe.segments.length <= 0) {
    throw new Error(`dashboard transcript route returned 0 visible-check segments for meeting ${meetingId}`);
  }
  const visibleSegment = transcriptProbe.segments
    .map((segment) => String(segment?.text || "").replace(/\s+/g, " ").trim())
    .find((text) => text.length >= 12);
  if (!visibleSegment) {
    throw new Error(`dashboard transcript route returned ${transcriptProbe.segments.length} segment(s), but no human-readable text for meeting ${meetingId}`);
  }
  const visibleNeedle = visibleSegment.slice(0, Math.min(40, visibleSegment.length));
  if (!normalized.includes(visibleNeedle)) {
    throw new Error(
      `dashboard transcript route returned ${transcriptProbe.segments.length} segment(s), but human-visible page text did not include transcript text for meeting ${meetingId}; expected snippet=${JSON.stringify(visibleNeedle)} page=${normalized.slice(0, 700)}`
    );
  }

  const recordingResponse = failures.find((line) => line.includes("/api/vexa/recordings/"));
  if (!recordingResponse?.startsWith("200 ")) {
    throw new Error(`recording master route was not fetched successfully for meeting ${meetingId}; recording responses=${failures.filter((line) => line.includes("/api/vexa/recordings/")).join(" | ") || "(none)"}`);
  }

  const hasPlaybackShell = /\b0:00\b/.test(normalized) || normalized.includes("Play") || normalized.includes("Pause");
  if (!hasPlaybackShell) {
    throw new Error(`completed recording did not render playback controls for meeting ${meetingId}: ${normalized.slice(0, 500)}`);
  }

  const audioState = await page.evaluate(() => {
    const audio = document.querySelector("audio");
    if (!audio) return null;
    return {
      src: audio.currentSrc || audio.src || "",
      readyState: audio.readyState,
      networkState: audio.networkState,
      duration: Number.isFinite(audio.duration) ? audio.duration : null,
      errorCode: audio.error?.code || null,
      errorMessage: audio.error?.message || "",
    };
  });
  if (!audioState?.src) {
    throw new Error(`completed recording rendered playback shell but no audio element/src for meeting ${meetingId}`);
  }
  if (audioState.errorCode) {
    throw new Error(`completed recording audio element has error for meeting ${meetingId}: ${JSON.stringify(audioState)}`);
  }
  if (!audioState.src.includes("/api/vexa/recordings/") || !audioState.src.includes("proxy=1")) {
    throw new Error(`completed recording audio src bypasses dashboard proxy for meeting ${meetingId}: ${JSON.stringify(audioState)}`);
  }
  if (audioState.src.includes("localhost:9100")) {
    throw new Error(`completed recording audio src points at local MinIO instead of dashboard proxy for meeting ${meetingId}: ${JSON.stringify(audioState)}`);
  }
  if (audioState.readyState < 1 || !audioState.duration || audioState.duration <= 0) {
    throw new Error(`completed recording audio metadata did not load for meeting ${meetingId}: ${JSON.stringify(audioState)}`);
  }

  const minioMediaResponse = failures.find((line) => line.includes("localhost:9100/") || line.includes("/vexa/recordings/"));
  if (minioMediaResponse && !(minioMediaResponse.startsWith("200 ") || minioMediaResponse.startsWith("206 "))) {
    throw new Error(`recording media object was not fetched successfully for meeting ${meetingId}: ${minioMediaResponse}`);
  }
  const hardFailures = failures.filter((line) => {
    if (line.startsWith("200 ")) return false;
    if ((line.includes("localhost:9100/") || line.includes("/vexa/recordings/")) && line.startsWith("206 ")) return false;
    return true;
  });
  if (hardFailures.length > 0) {
    throw new Error(`unexpected dashboard recording route failures: ${hardFailures.slice(0, 5).join(" | ")}`);
  }

  console.log(`PASS ${dashboardUrl}/meetings/${meetingId} rendered completed recording playback and visible transcript text (${transcriptProbe.segments.length} segment(s)), not processing`);
} finally {
  await browser.close();
}
