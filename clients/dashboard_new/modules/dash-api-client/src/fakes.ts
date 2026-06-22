/**
 * createFakeApiClient — an in-memory ApiClient that serves api.v1 GOLDEN-shaped responses.
 *
 * No network, no fetch: every method returns a fresh clone of a seeded shape that conforms to the
 * sealed api.v1 schemas (verified by api-client.test.ts). Used to develop/test the dashboard UI
 * (storybook, component tests) without a live backend. `postBot` mints a `requested` meeting and
 * `deleteBot` flips it to `stopping`, so the seam behaves like the real lifecycle.
 */
import type {
  Platform,
  MeetingListResponse,
  MeetingResponse,
  TranscriptionResponse,
  RecordingMaster,
} from "@vexa/dash-contracts";

import type {
  ApiClient,
  BotRequest,
  GetMeetingsParams,
  RecordingMasterType,
} from "./ports.js";

export interface FakeSeed {
  meetings?: MeetingResponse[];
  transcript?: TranscriptionResponse;
  recordingMaster?: RecordingMaster;
}

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

/** The default seed: two meetings (one active, one completed), one transcript, one recording master. */
function defaultSeed(): Required<FakeSeed> {
  const meetings: MeetingResponse[] = [
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
    {
      id: 43,
      user_id: 7,
      platform: "google_meet",
      native_meeting_id: "klm-nopq-rst",
      constructed_meeting_url: "https://meet.google.com/klm-nopq-rst",
      status: "completed",
      bot_container_id: null,
      start_time: "2026-06-19T14:00:00Z",
      end_time: "2026-06-19T14:32:00Z",
      created_at: "2026-06-19T13:59:00Z",
      updated_at: "2026-06-19T14:32:10Z",
    },
  ];

  const transcript: TranscriptionResponse = {
    id: 42,
    platform: "google_meet",
    native_meeting_id: "abc-defg-hij",
    constructed_meeting_url: "https://meet.google.com/abc-defg-hij",
    status: "active",
    start_time: "2026-06-20T09:00:00Z",
    end_time: null,
    segments: [
      {
        start: 1.0,
        end: 2.5,
        text: "This is Anna.",
        language: "en",
        speaker: "spk-Anna",
        completed: true,
      },
      {
        start: 2.6,
        end: 5.2,
        text: "And this is Ben, thanks for joining.",
        language: "en",
        speaker: "spk-Ben",
        completed: true,
      },
    ],
  };

  const recordingMaster: RecordingMaster = {
    id: 1001,
    type: "mixed",
    storage_path: "s3://vexa-recordings/42/master.webm",
    media_file_id: 5001,
    raw_url: "https://api.vexa.ai/recordings/1001/media/5001/raw",
    duration_seconds: 1920,
  };

  return { meetings, transcript, recordingMaster };
}

export function createFakeApiClient(seed?: FakeSeed): ApiClient {
  const base = defaultSeed();
  const meetings: MeetingResponse[] = seed?.meetings ?? base.meetings;
  const transcript: TranscriptionResponse = seed?.transcript ?? base.transcript;
  const recordingMaster: RecordingMaster = seed?.recordingMaster ?? base.recordingMaster;
  // Mutable store so postBot/deleteBot evolve state like the real lifecycle.
  const store: MeetingResponse[] = clone(meetings);
  let nextId = store.reduce((m, x) => Math.max(m, Number(x.id) || 0), 0) + 1;

  return {
    async getMeetings(params?: GetMeetingsParams): Promise<MeetingListResponse> {
      let list = clone(store);
      if (params?.status) list = list.filter((m) => m.status === params.status);
      if (params?.platform) list = list.filter((m) => m.platform === params.platform);
      return { meetings: list };
    },

    async getMeeting(id: number | string): Promise<MeetingResponse> {
      const found = store.find((m) => String(m.id) === String(id));
      if (!found) throw new Error(`fake api.v1: no meeting ${id}`);
      return clone(found);
    },

    async getTranscripts(
      _platform: Platform | string,
      _nativeId: string,
    ): Promise<TranscriptionResponse> {
      return clone(transcript);
    },

    async getRecordingMaster(
      _recordingId: number | string,
      _type?: RecordingMasterType,
    ): Promise<RecordingMaster> {
      return clone(recordingMaster);
    },

    async postBot(req: BotRequest): Promise<MeetingResponse> {
      const now = new Date().toISOString();
      const meeting: MeetingResponse = {
        id: nextId++,
        user_id: 7,
        platform: String(req.platform),
        native_meeting_id: req.native_meeting_id ?? null,
        constructed_meeting_url: req.meeting_url ?? null,
        status: "requested",
        bot_container_id: null,
        start_time: null,
        end_time: null,
        created_at: now,
        updated_at: now,
      };
      store.push(clone(meeting));
      return clone(meeting);
    },

    async deleteBot(platform: Platform | string, nativeId: string): Promise<void> {
      const found = store.find(
        (m) => m.platform === String(platform) && m.native_meeting_id === nativeId,
      );
      if (found) found.status = "stopping";
    },
  };
}
