/**
 * createHttpApiClient — the REAL api.v1 ApiClient over HTTP.
 *
 * It hits the sealed api.v1 paths off `baseUrl`. Response validation is INJECTED, never hard-wired:
 * pass `validate` (the node-only `@vexa/dash-contracts/validate` `validateApiShape`) and a drifted
 * backend fails LOUD here; omit it (the browser default) and the client is a pure typed pass-through —
 * so the fs-backed ajv validator is never dragged into the browser bundle. `fetchImpl` is injected too
 * (default global `fetch`) so tests drive a stub; no DOM/`window` dependency.
 */
// Types only — the `.` front door is fully erased, so the browser bundle carries zero contracts runtime.
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
  FetchImpl,
  FetchResponse,
  GetMeetingsParams,
  RecordingMasterType,
} from "./ports.js";

/** A response-shape validator: `{valid}` (+ `errors`) for a body against a named sealed api.v1 shape. */
export type ApiShapeValidator = (shape: string, body: unknown) => { valid: boolean; errors?: string };

export interface CreateHttpApiClientOptions {
  /** Base URL of the api.v1 gateway, e.g. "https://api.vexa.ai" or "http://localhost:8056". */
  baseUrl: string;
  /** Injected fetch (default: global `fetch`). Tests pass a stub returning goldens. */
  fetchImpl?: FetchImpl;
  /** Optional auth header value sent as `X-API-Key` on every request. */
  apiKey?: string;
  /**
   * Optional response validator — pass `@vexa/dash-contracts/validate`'s `validateApiShape` to fail
   * LOUD on a drifted backend. Omit it in the browser: the validator is fs-backed (node-only), and a
   * typed pass-through is the right browser behaviour (shape drift is caught at L1/L2, not per-request).
   */
  validate?: ApiShapeValidator;
}

/** Trim a trailing slash so `baseUrl + "/meetings"` never doubles up. */
function normBase(base: string): string {
  return base.endsWith("/") ? base.slice(0, -1) : base;
}

function encode(seg: string | number): string {
  return encodeURIComponent(String(seg));
}

/** A typed api.v1 GET: throws on non-2xx, then conforms the body to `shape` when a validator is given. */
async function getJson<T>(
  fetchImpl: FetchImpl,
  url: string,
  headers: Record<string, string>,
  shape: string,
  validate?: ApiShapeValidator,
): Promise<T> {
  const res = await fetchImpl(url, { method: "GET", headers });
  return parseValidated<T>(res, url, "GET", shape, validate);
}

async function parseValidated<T>(
  res: FetchResponse,
  url: string,
  method: string,
  shape: string,
  validate?: ApiShapeValidator,
): Promise<T> {
  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      /* body already consumed / not text — the status is enough */
    }
    throw new Error(
      `api.v1 ${method} ${url} → ${res.status}${res.statusText ? " " + res.statusText : ""}${
        detail ? ": " + detail.slice(0, 200) : ""
      }`,
    );
  }
  const body = (await res.json()) as unknown;
  if (validate) {
    const { valid, errors } = validate(shape, body);
    if (!valid) {
      throw new Error(`api.v1 ${method} ${url} response failed ${shape} validation: ${errors}`);
    }
  }
  return body as T;
}

export function createHttpApiClient(opts: CreateHttpApiClientOptions): ApiClient {
  const base = normBase(opts.baseUrl);
  const fetchImpl: FetchImpl =
    opts.fetchImpl ?? ((globalThis as { fetch?: unknown }).fetch as FetchImpl | undefined)!;
  if (typeof fetchImpl !== "function") {
    throw new Error("createHttpApiClient: no fetchImpl provided and global fetch is unavailable");
  }
  const headers = (): Record<string, string> => {
    const h: Record<string, string> = { Accept: "application/json" };
    if (opts.apiKey) h["X-API-Key"] = opts.apiKey;
    return h;
  };

  return {
    async getMeetings(params?: GetMeetingsParams): Promise<MeetingListResponse> {
      const qs = new URLSearchParams();
      if (params) {
        for (const [k, v] of Object.entries(params)) {
          if (v !== undefined && v !== null) qs.set(k, String(v));
        }
      }
      const suffix = qs.toString() ? `?${qs.toString()}` : "";
      return getJson<MeetingListResponse>(
        fetchImpl,
        `${base}/meetings${suffix}`,
        headers(),
        "MeetingListResponse",
        opts.validate,
      );
    },

    async getMeeting(id: number | string): Promise<MeetingResponse> {
      return getJson<MeetingResponse>(
        fetchImpl,
        `${base}/meetings/${encode(id)}`,
        headers(),
        "MeetingResponse",
        opts.validate,
      );
    },

    async getTranscripts(
      platform: Platform | string,
      nativeId: string,
    ): Promise<TranscriptionResponse> {
      return getJson<TranscriptionResponse>(
        fetchImpl,
        `${base}/transcripts/${encode(platform)}/${encode(nativeId)}`,
        headers(),
        "TranscriptionResponse",
        opts.validate,
      );
    },

    async getRecordingMaster(
      recordingId: number | string,
      type?: RecordingMasterType,
    ): Promise<RecordingMaster> {
      const qs = type ? `?type=${encode(type)}` : "";
      const url = `${base}/recordings/${encode(recordingId)}/master${qs}`;
      // `/master` is not a sealed api.v1 component, so it has no validateApiShape validator —
      // we read the typed body directly (the dashboard's typed projection from dash-contracts).
      const res = await fetchImpl(url, { method: "GET", headers: headers() });
      if (!res.ok) {
        let detail = "";
        try {
          detail = await res.text();
        } catch {
          /* status is enough */
        }
        throw new Error(
          `api.v1 GET ${url} → ${res.status}${detail ? ": " + detail.slice(0, 200) : ""}`,
        );
      }
      return (await res.json()) as RecordingMaster;
    },

    async postBot(req: BotRequest): Promise<MeetingResponse> {
      const url = `${base}/bots`;
      const res: FetchResponse = await fetchImpl(url, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        body: JSON.stringify(req),
      });
      return parseValidated<MeetingResponse>(res, url, "POST", "MeetingResponse", opts.validate);
    },

    async deleteBot(platform: Platform | string, nativeId: string): Promise<void> {
      const url = `${base}/bots/${encode(platform)}/${encode(nativeId)}`;
      const res = await fetchImpl(url, { method: "DELETE", headers: headers() });
      if (!res.ok) {
        let detail = "";
        try {
          detail = await res.text();
        } catch {
          /* status is enough */
        }
        throw new Error(
          `api.v1 DELETE ${url} → ${res.status}${detail ? ": " + detail.slice(0, 200) : ""}`,
        );
      }
      // DELETE /bots returns the (stopping) MeetingResponse; the port surfaces void — body discarded.
    },
  };
}
