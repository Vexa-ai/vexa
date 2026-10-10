/** Model choice (ADR-0042) — the client edges of the model picker.
 *
 *  The deployment's catalog is agent-api's (`GET /api/models/catalog`, the `models.v1` ModelList):
 *  only the models THIS person may pick, the one they run on before picking, and — for one chat —
 *  that chat's own pick. It carries no endpoint and no credential, so nothing here ever holds one.
 *  A pick is stored per chat by agent-api (`POST /api/chat/model`), which refuses one the next turn
 *  could not run with a typed fault; the person's default is a Settings → Models field
 *  (`default_model`, admin-api, through the same `/api/user/models` edge the settings form uses). */
import { getJson } from "./apiClient";
import { setModelPrefs } from "./settingsApi";

export type ModelEntry = {
  id: string;
  display_name: string;
  provider: string;
  adapter: "openai_compatible" | "openrouter" | "anthropic" | "custom";
  harness: string;
  capabilities: { tool_calling: boolean; streaming: boolean; context_tokens?: number | null };
  access: "everyone" | "admins";
  default: boolean;
};

export type ModelList = {
  models: ModelEntry[];
  /** what this person runs on before they pick — null when the list is empty */
  default: string | null;
  /** the named chat's own pick; null = it follows the default */
  selected?: string | null;
};

export async function getModelCatalog(session?: string): Promise<ModelList> {
  const q = session ? `?session=${encodeURIComponent(session)}` : "";
  return getJson<ModelList>(`/api/models/catalog${q}`, { cache: "no-store" });
}

/** Pin one chat to a model; `""` puts it back on the person's default. */
export async function setChatModel(session: string, model: string): Promise<{ session: string; model: string | null; changed: boolean }> {
  return getJson(`/api/chat/model`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session, model }),
  });
}

/** The model new and unpicked chats run on, for this person (Settings → Models `default_model`). */
export async function setDefaultModel(model: string): Promise<void> {
  await setModelPrefs({ default_model: model });
}

/** The model a chat runs on: its own pick, else the person's default. */
export function effectiveModel(list: ModelList): ModelEntry | null {
  const id = list.selected || list.default;
  return list.models.find((m) => m.id === id) ?? null;
}
