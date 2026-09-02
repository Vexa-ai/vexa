/** Settings → Models client edges. Per-user prefs ride the authenticated catch-all proxy to the
 *  gateway (`/api/user/models`, `/api/user/transcription` — admin-api behind it, secrets masked
 *  on every read-back). The GLOBAL defaults ride the admin-gated terminal route
 *  (`/api/admin/settings/{key}` — 404 for non-admins, indistinguishable from absent). */
import { ApiError } from "./apiClient";

export type ModelPrefs = {
  mode?: "subscription" | "custom" | null;
  model?: string | null;
  meeting_model?: string | null;
  effort?: string | null; // reasoning-effort pin (low|medium|high|xhigh) for the agent harness
  base_url?: string | null;
  api_key_set?: boolean;
  api_key?: string | null; // masked on read (********abcd) — write-only in the clear
};

export type TranscriptionPrefs = {
  url?: string | null;
  token_set?: boolean;
  token?: string | null; // masked on read — write-only in the clear
};

/** Global platform settings carry the SAME fields unmasked (admin tier). */
export type GlobalSetting = Record<string, string>;

async function jsonOrThrow(res: Response) {
  if (!res.ok) {
    // Structured failure (P18): carry status + detail so the presenter maps it to user truth.
    let detail = "";
    try { detail = ((await res.json()) as { detail?: string; error?: string }).detail || ""; } catch { /* body not json */ }
    throw new ApiError(res.status, detail, res.url);
  }
  return res.json();
}

export async function getModelPrefs(): Promise<ModelPrefs> {
  return jsonOrThrow(await fetch("/api/user/models", { cache: "no-store" }));
}

export async function setModelPrefs(update: Partial<Record<keyof ModelPrefs, string>>): Promise<ModelPrefs> {
  return jsonOrThrow(await fetch("/api/user/models", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(update),
  }));
}

export async function getTranscriptionPrefs(): Promise<TranscriptionPrefs> {
  return jsonOrThrow(await fetch("/api/user/transcription", { cache: "no-store" }));
}

export async function setTranscriptionPrefs(update: { url?: string; token?: string }): Promise<TranscriptionPrefs> {
  return jsonOrThrow(await fetch("/api/user/transcription", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(update),
  }));
}

/** The admin-writable platform-settings keys: the two config domains + the first-run wizard's
 *  durable "setup" state. */
export type GlobalSettingKey = "models" | "transcription" | "setup";

// ── the company layer (`_global`) ───────────────────────────────────────────────────────────────
/** The five files the platform `_global` workspace must hold before this instance serves anybody:
 *  who the company is, what it stands for, what it is working toward, how it is structured, and what
 *  is still missing. Listed here only so the wizard can name the ones that are absent — the SERVER
 *  decides `global_setup`, never this list; a client-side recomputation would be a second opinion on
 *  a gate that must have exactly one. */
export const COMPANY_LAYER_FILES = ["README.md", "PRINCIPLES.md", "OBJECTIVES.md", "STRUCTURE.md", "MISSING.md"] as const;

/** The company layer's live state, as agent-api reports it. `global_setup` is the gate itself.
 *
 *  `reasons` is the field the UI should actually SHOW: plain sentences from the server saying what
 *  is not yet true. The card renders them verbatim instead of composing its own wording, because a
 *  client that phrases the gate's reasons itself is a second author of the gate's meaning — it drifts
 *  the moment the server learns a new reason, and drifts silently, since a missing sentence looks
 *  exactly like a satisfied condition. `company` is null until the gate lifts, by design. */
export type GlobalState = {
  global_setup: "completed" | "missing";
  company: string | null;
  present: string[];
  missing_files: string[];
  /** Plain sentences naming what is still not true. Render verbatim. */
  reasons: string[];
  is_repo: boolean;
  commits: number;
  ready_to_accept: boolean;
  you_are_admin: boolean;
  /** The server's own copy of the refusal sentence — carried so a caller never has to retype it. */
  gate_sentence: string;
};

/** Read the company layer's state. Rides the authenticated catch-all proxy — `/api/global/state` →
 *  gateway `/agent/global/state` — the same edge `/api/workspace/*` uses, so the gateway resolves
 *  the caller's api-key to a user and agent-api answers for the real identity.
 *
 *  Throws (ApiError) on a non-2xx, and normalises an unrecognised body towards "missing". Note that
 *  this is the OPPOSITE fail-safe direction from the sign-in gate in api/auth/adminApi.ts, and
 *  deliberately so: there, guessing wrong locks every user out of a working instance, so it guesses
 *  "completed"; here, guessing wrong only makes the admin's own card keep waiting a few seconds
 *  longer, while guessing "completed" would declare setup finished on a garbled response and let the
 *  admin walk away from an instance that still serves nobody. Each direction is chosen against the
 *  damage its own mistake does. */
export async function getGlobalState(): Promise<GlobalState> {
  const body = (await jsonOrThrow(await fetch("/api/global/state", { cache: "no-store" }))) as Partial<GlobalState>;
  const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((s): s is string => typeof s === "string") : []);
  return {
    global_setup: body.global_setup === "completed" ? "completed" : "missing",
    company: typeof body.company === "string" && body.company.trim() ? body.company : null,
    present: strings(body.present),
    missing_files: strings(body.missing_files),
    reasons: strings(body.reasons),
    is_repo: body.is_repo === true,
    commits: typeof body.commits === "number" ? body.commits : 0,
    ready_to_accept: body.ready_to_accept === true,
    you_are_admin: body.you_are_admin === true,
    gate_sentence: typeof body.gate_sentence === "string" ? body.gate_sentence : "",
  };
}

/** null ⇒ caller is not an admin (the route 404s) — the global card simply doesn't render. */
export async function getGlobalSetting(key: GlobalSettingKey): Promise<GlobalSetting | null> {
  const res = await fetch(`/api/admin/settings/${key}`, { cache: "no-store" });
  if (res.status === 404) return null;
  const body = await jsonOrThrow(res) as { value?: GlobalSetting };
  return body.value ?? {};
}

export async function setGlobalSetting(key: GlobalSettingKey, update: GlobalSetting): Promise<GlobalSetting> {
  const res = await fetch(`/api/admin/settings/${key}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(update),
  });
  const body = await jsonOrThrow(res) as { value?: GlobalSetting };
  return body.value ?? {};
}

/** On-demand credential tests (agent-api /api/{models,transcription}/test via the catch-all →
 *  gateway /agent/* edge). They test the EFFECTIVE config — the same user > global > env
 *  resolution a real turn / bot spawn applies — and fail LOUD with the remedy in `summary`. */
export type ConfigTestResult = {
  ok: boolean;
  summary: string;
  mode?: string;          // models: "subscription" | "custom"
  source?: string;        // transcription: "settings" | "env"
  expires_in_hours?: number;
  account?: string;
  balance?: number | null;
};

export async function testModels(): Promise<ConfigTestResult> {
  return jsonOrThrow(await fetch("/api/models/test", { cache: "no-store" }));
}

export async function testTranscription(): Promise<ConfigTestResult> {
  return jsonOrThrow(await fetch("/api/transcription/test", { cache: "no-store" }));
}
