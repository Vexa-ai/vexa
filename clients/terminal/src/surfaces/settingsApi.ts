/** Settings → Models client edges. Per-user prefs ride the authenticated catch-all proxy to the
 *  gateway (`/api/user/models`, `/api/user/transcription` — admin-api behind it, secrets masked
 *  on every read-back). The GLOBAL defaults ride the admin-gated terminal route
 *  (`/api/admin/settings/{key}` — 404 for non-admins, indistinguishable from absent). */
import { ApiError } from "./apiClient";

export type ModelPrefs = {
  mode?: "subscription" | "custom" | null;
  model?: string | null;
  effort?: string | null; // reasoning-effort pin (low|medium|high|xhigh) for the agent harness
  base_url?: string | null;
  api_key_set?: boolean;
  api_key?: string | null; // masked on read (********abcd) — write-only in the clear
  default_model?: string | null; // the model-catalog id new and unpicked chats run on (ADR-0043)
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

/** The admin-writable platform-settings keys: the two config domains. */
export type GlobalSettingKey = "models" | "transcription";

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

/** WHO MAY SIGN IN (Vexa-ai/vexa#1783) — the admin-edited half of the instance's sign-in allow-list,
 *  over the same admin-gated route as the global defaults (`/api/admin/settings/signin`, 404 for
 *  non-admins). `allow` is what the admin wrote; `env` is the deployment's `VEXA_SIGNIN_ALLOW`, shown
 *  read-only because it is changed where it is set, not here; `envProblems` are env entries that can
 *  never match (a typo like `example.com` without its `@`). The effective list is both. */
export type SigninAllow = { allow: string; env: string; envProblems: string[] };

/** null ⇒ caller is not an admin (the route 404s) — the Sign-in section simply doesn't render. */
export async function getSigninAllow(): Promise<SigninAllow | null> {
  const res = await fetch("/api/admin/settings/signin", { cache: "no-store" });
  if (res.status === 404) return null;
  const body = await jsonOrThrow(res) as { value?: { allow?: string }; env?: { allow?: string }; env_problems?: string[] };
  return { allow: body.value?.allow ?? "", env: body.env?.allow ?? "", envProblems: body.env_problems ?? [] };
}

/** Replace the admin-edited list. Entries are exact addresses or `@domain`, separated by commas or
 *  new lines; admin-api validates all-or-nothing (422 names every bad entry) and returns the list in
 *  its canonical form. An empty string clears it. */
export async function setSigninAllow(allow: string): Promise<string> {
  const res = await fetch("/api/admin/settings/signin", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ allow }),
  });
  const body = await jsonOrThrow(res) as { value?: { allow?: string } };
  return body.value?.allow ?? "";
}

/** One entry per line, for the editor — the stored form is ", "-joined. */
export function allowLines(stored: string): string {
  return stored.split(/[\s,;]+/).map((e) => e.trim()).filter(Boolean).join("\n");
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
