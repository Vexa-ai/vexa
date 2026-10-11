/** Model choice (ADR-0043) — the client edges of the model picker.
 *
 *  The deployment's catalog is agent-api's (`GET /api/models/catalog`, the `models.v1` ModelList):
 *  only the models THIS person may pick, the one they run on before picking, and — for one chat —
 *  that chat's own pick. It carries no endpoint and no credential, so nothing here ever holds one.
 *  A pick is stored per chat by agent-api (`POST /api/chat/model`), which refuses one the next turn
 *  could not run with a typed fault; the person's default is a Settings → Models field
 *  (`default_model`, admin-api, through the same `/api/user/models` edge the settings form uses).
 *
 *  EFFORT, per chat, beside the model: each model lists the levels it offers
 *  (`capabilities.reasoning_efforts`, empty when it has no effort control) and the one it runs at
 *  until the chat picks (`default_effort`). The pick is stored with the model, so changing the model
 *  clears it. */
import { getJson } from "./apiClient";
import { setModelPrefs } from "./settingsApi";

/** The effort vocabulary (`models.v1#/$defs/ReasoningEffort`). */
export type Effort = "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

/** The provider kinds a catalog model runs on (`models.v1#/$defs/Adapter`). Every one is a real
 *  model named by its id at the provider; there is no placeholder kind (founder 2026-10-11). */
export const CATALOG_ADAPTERS = ["openai_compatible", "openrouter", "anthropic"] as const;
export type Adapter = (typeof CATALOG_ADAPTERS)[number];

export type ModelEntry = {
  id: string;
  display_name: string;
  provider: string;
  adapter: Adapter;
  harness: string;
  capabilities: {
    tool_calling: boolean; streaming: boolean; context_tokens?: number | null;
    /** the levels a chat may pick for this model; absent or empty = no effort control */
    reasoning_efforts?: Effort[];
    default_effort?: Effort | null;
  };
  access: "everyone" | "admins";
  default: boolean;
};

export type ModelList = {
  models: ModelEntry[];
  /** what this person runs on before they pick — null when the list is empty */
  default: string | null;
  /** the named chat's own pick; null = it follows the default */
  selected?: string | null;
  /** the named chat's own effort pick; null = the model's default_effort */
  selected_effort?: Effort | null;
};

/** The person's model list. Only concrete catalog models are kept: an entry of any other kind (an
 *  older server's "own endpoint" placeholder, which names no model) is never offered as a pick. */
export async function getModelCatalog(session?: string): Promise<ModelList> {
  const q = session ? `?session=${encodeURIComponent(session)}` : "";
  const list = await getJson<ModelList>(`/api/models/catalog${q}`, { cache: "no-store" });
  if (!Array.isArray(list?.models)) return list;
  return { ...list, models: list.models.filter((m) => (CATALOG_ADAPTERS as readonly string[]).includes(m.adapter)) };
}

/** Pin one chat to a model at an effort level; `""` puts it back on the person's default model,
 *  and an empty effort on the model's own default level. */
export async function setChatModel(session: string, model: string, effort = ""): Promise<{ session: string; model: string | null; effort?: Effort | null; changed: boolean }> {
  return getJson(`/api/chat/model`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(effort ? { session, model, effort } : { session, model }),
  });
}

/** The levels a model offers — empty when it has no effort control. */
export function effortsOf(m: ModelEntry | null): Effort[] {
  return m?.capabilities.reasoning_efforts ?? [];
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
