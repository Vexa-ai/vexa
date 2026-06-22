/**
 * @vexa/dash-api-client — the api.v1 REST client behind a PORT.
 *
 * The dashboard reads the backend through ONE interface, `ApiClient` (ports.ts). Two implementations
 * live behind it, swappable without touching a caller:
 *   • createHttpApiClient — the real client over HTTP (injected fetch; validates every response
 *     against the sealed api.v1 shapes via @vexa/dash-contracts).
 *   • createFakeApiClient — in-memory api.v1 golden-shaped responses (no network), for UI dev/tests.
 *
 * Shapes come from @vexa/dash-contracts (the consumed-contract seam) — this brick never redefines
 * them; it only fetches/serves and validates them.
 */
export type {
  ApiClient,
  BotRequest,
  GetMeetingsParams,
  RecordingMasterType,
  FetchImpl,
  FetchResponse,
} from "./ports.js";

export { createHttpApiClient } from "./adapters.js";
export type { CreateHttpApiClientOptions } from "./adapters.js";

export { createFakeApiClient } from "./fakes.js";
export type { FakeSeed } from "./fakes.js";
