/**
 * L2 — pins the api.v1 REST client to the SEALED contracts. Exit 1 on any failure.
 *
 *   1. createFakeApiClient(): getMeetings()/getMeeting()/getTranscripts()/getRecordingMaster()/
 *      postBot() return shapes that VALIDATE against api.v1 (via @vexa/dash-contracts validateApiShape).
 *      RecordingMaster has no sealed component, so it's checked structurally.
 *   2. createHttpApiClient() with a STUB fetch returning a golden parses + validates the body, hits the
 *      correct api.v1 path/method, and throws LOUD on a drifted (invalid) response and on non-2xx.
 *   3. deleteBot drives DELETE; the fake lifecycle (postBot → requested, deleteBot → stopping) holds.
 */
// The validator is the node-only `/validate` subpath (fs-backed ajv) — injected into the HTTP client
// below, exactly as a node test/tool would. The browser never imports it (the client defaults to a
// typed pass-through), which is what keeps the fs validator out of the browser bundle.
import { validateApiShape } from "@vexa/dash-contracts/validate";

import { createFakeApiClient } from "./fakes.js";
import { createHttpApiClient } from "./adapters.js";
import type { FetchImpl, FetchResponse } from "./ports.js";

let failed = 0;
const ok = (label: string, cond: boolean, detail = "") => {
  console.log(`  ${cond ? "✓" : "✗"} ${label}${cond ? "" : detail ? " — " + detail : ""}`);
  if (!cond) failed++;
};
const conforms = (shape: string, obj: unknown): { valid: boolean; errors: string } =>
  validateApiShape(shape, obj);

/** Build a stub FetchResponse from a JSON body (default 200 OK). */
function jsonResponse(body: unknown, init?: { ok?: boolean; status?: number }): FetchResponse {
  const status = init?.status ?? 200;
  const okFlag = init?.ok ?? (status >= 200 && status < 300);
  return {
    ok: okFlag,
    status,
    statusText: okFlag ? "OK" : "ERR",
    async json() {
      return body;
    },
    async text() {
      return typeof body === "string" ? body : JSON.stringify(body);
    },
  };
}

async function main() {
  // ── 1) the fake client returns api.v1-conformant shapes ──────────────────────────────────────
  console.log("createFakeApiClient → api.v1 shapes:");
  const fake = createFakeApiClient();

  const list = await fake.getMeetings();
  {
    const { valid, errors } = conforms("MeetingListResponse", list);
    ok("getMeetings() ≡ MeetingListResponse", valid, errors);
    ok("getMeetings() has >= 1 meeting", list.meetings.length >= 1);
  }

  const meeting = await fake.getMeeting(42);
  {
    const { valid, errors } = conforms("MeetingResponse", meeting);
    ok("getMeeting(42) ≡ MeetingResponse", valid, errors);
    ok('getMeeting(42).id == 42', meeting.id === 42);
  }

  const transcript = await fake.getTranscripts("google_meet", "abc-defg-hij");
  {
    const { valid, errors } = conforms("TranscriptionResponse", transcript);
    ok("getTranscripts() ≡ TranscriptionResponse", valid, errors);
    ok(
      `getTranscripts().segments[0].text read ("${transcript.segments[0]?.text}")`,
      typeof transcript.segments[0]?.text === "string" && transcript.segments[0].text.length > 0,
    );
  }

  const master = await fake.getRecordingMaster(1001, "mixed");
  {
    // RecordingMaster is NOT a sealed api.v1 component — check the dashboard's typed projection.
    const okShape =
      master != null &&
      typeof master.storage_path === "string" &&
      typeof master.raw_url === "string" &&
      typeof master.duration_seconds === "number" &&
      master.type === "mixed";
    ok("getRecordingMaster(1001) has the master projection floor", okShape);
  }

  // ── fake lifecycle: postBot → requested, deleteBot → stopping ─────────────────────────────────
  console.log("createFakeApiClient → lifecycle:");
  const created = await fake.postBot({ platform: "google_meet", native_meeting_id: "new-meet-xyz" });
  {
    const { valid, errors } = conforms("MeetingResponse", created);
    ok("postBot() ≡ MeetingResponse", valid, errors);
    ok(`postBot() status == requested ("${created.status}")`, created.status === "requested");
  }
  await fake.deleteBot("google_meet", "new-meet-xyz");
  {
    const afterList = await fake.getMeetings();
    const stopped = afterList.meetings.find((m) => m.native_meeting_id === "new-meet-xyz");
    ok(`deleteBot() flips status to stopping ("${stopped?.status}")`, stopped?.status === "stopping");
  }

  // ── 2) the HTTP client parses + validates a golden via a STUB fetch ───────────────────────────
  console.log("createHttpApiClient → stub fetch:");
  const goldenList = {
    meetings: [
      {
        id: 42,
        user_id: 7,
        platform: "google_meet",
        native_meeting_id: "abc-defg-hij",
        constructed_meeting_url: "https://meet.google.com/abc-defg-hij",
        status: "active",
        bot_container_id: "mtg-abc-defg-hij-bot",
        start_time: "2026-06-20T09:00:00Z",
        end_time: null,
        created_at: "2026-06-20T08:59:00Z",
        updated_at: "2026-06-20T09:00:05Z",
      },
    ],
  };
  const goldenMeeting = goldenList.meetings[0];

  // capture the calls the client makes so we can assert path + method
  const calls: Array<{ url: string; method?: string; body?: string }> = [];
  const stubFetch: FetchImpl = async (url, init) => {
    calls.push({ url, method: init?.method, body: init?.body });
    const path = url.split("?")[0];
    if (/\/meetings\/42$/.test(path)) return jsonResponse(goldenMeeting);
    if (path.endsWith("/meetings")) return jsonResponse(goldenList);
    if (path.endsWith("/bots") && init?.method === "POST") return jsonResponse(goldenMeeting);
    if (path.includes("/bots/") && init?.method === "DELETE")
      return jsonResponse(goldenMeeting, { status: 200 });
    return jsonResponse({ detail: "not found" }, { status: 404 });
  };

  const http = createHttpApiClient({
    baseUrl: "https://api.example/",
    fetchImpl: stubFetch,
    validate: validateApiShape,
  });

  const httpList = await http.getMeetings({ status: "active" });
  {
    const { valid, errors } = conforms("MeetingListResponse", httpList);
    ok("http.getMeetings() parsed + ≡ MeetingListResponse", valid, errors);
    ok(
      "http.getMeetings() hit GET /meetings?status=active",
      calls.some((c) => c.url === "https://api.example/meetings?status=active" && c.method === "GET"),
    );
  }

  const httpMeeting = await http.getMeeting(42);
  {
    const { valid, errors } = conforms("MeetingResponse", httpMeeting);
    ok("http.getMeeting(42) parsed + ≡ MeetingResponse", valid, errors);
    ok(
      "http.getMeeting(42) hit GET /meetings/42",
      calls.some((c) => c.url === "https://api.example/meetings/42" && c.method === "GET"),
    );
  }

  const posted = await http.postBot({ platform: "google_meet", native_meeting_id: "abc-defg-hij" });
  {
    const { valid } = conforms("MeetingResponse", posted);
    ok("http.postBot() parsed + ≡ MeetingResponse", valid);
    const postCall = calls.find((c) => c.url === "https://api.example/bots" && c.method === "POST");
    ok("http.postBot() hit POST /bots", !!postCall);
    ok(
      "http.postBot() sent JSON body with platform",
      !!postCall?.body && JSON.parse(postCall.body).platform === "google_meet",
    );
  }

  {
    await http.deleteBot("google_meet", "abc-defg-hij");
    ok(
      "http.deleteBot() hit DELETE /bots/google_meet/abc-defg-hij",
      calls.some(
        (c) =>
          c.url === "https://api.example/bots/google_meet/abc-defg-hij" && c.method === "DELETE",
      ),
    );
  }

  // ── 3) WITH a validator: throws LOUD on a drifted body; non-2xx throws regardless ──────────────
  console.log("createHttpApiClient → fails loud (validator injected):");
  {
    const driftFetch: FetchImpl = async () => jsonResponse({ meetings: [{ id: "not-a-number" }] });
    const driftClient = createHttpApiClient({
      baseUrl: "https://api.example",
      fetchImpl: driftFetch,
      validate: validateApiShape,
    });
    let threw = false;
    try {
      await driftClient.getMeetings();
    } catch {
      threw = true;
    }
    ok("getMeetings() throws on a drifted (api.v1-invalid) body when validating", threw);
  }
  {
    const errFetch: FetchImpl = async () =>
      jsonResponse({ detail: "boom" }, { status: 500, ok: false });
    const errClient = createHttpApiClient({ baseUrl: "https://api.example", fetchImpl: errFetch });
    let threw = false;
    try {
      await errClient.getMeeting(99);
    } catch {
      threw = true;
    }
    ok("getMeeting() throws on a non-2xx response (no validator needed)", threw);
  }

  // ── 4) browser default: NO validator → a drifted body passes through (typed pass-through) ───────
  console.log("createHttpApiClient → browser pass-through (no validator):");
  {
    const driftFetch: FetchImpl = async () => jsonResponse({ meetings: [{ id: "not-a-number" }] });
    const passClient = createHttpApiClient({ baseUrl: "https://api.example", fetchImpl: driftFetch });
    let threw = false;
    try {
      await passClient.getMeetings();
    } catch {
      threw = true;
    }
    ok("getMeetings() does NOT throw on drift without a validator (browser-safe pass-through)", !threw);
  }

  // ── verdict ──────────────────────────────────────────────────────────────────────────────────
  console.log(
    failed
      ? `\ndash-api-client: ${failed} check(s) FAILED`
      : `\ndash-api-client: all checks pass (≡ sealed api.v1 via dash-contracts)`,
  );
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.error("dash-api-client: test crashed:", e);
  process.exit(1);
});
