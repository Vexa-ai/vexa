/** Public profiles (R4, feature `public_profiles`): the person's own profile and the directory are
 *  identity's (`/api/user/profile*`, `/api/user/directory/*` → the gateway → admin-api); a contact's
 *  link to a profile is crm-api's (`/api/crm` operations `profile/read`, `profile/link`). */

export const PROFILE_FIELDS = ["name", "photo", "role", "company", "links", "bio"] as const;
export type ProfileFieldName = (typeof PROFILE_FIELDS)[number];
export const FIELD_LABEL: Record<ProfileFieldName, string> = {
  name: "Name", photo: "Photo", role: "Role", company: "Company", links: "Links", bio: "Short bio",
};
export type OwnField = { value: string | string[]; public: boolean };
export type OwnProfile = { handle: string | null; published: boolean; linked_by: number;
  fields: Record<ProfileFieldName, OwnField>;
  company_verification: { domain: string; txt: string; verified_at?: number; method?: string } | null };
export type PublicProfile = { handle: string; fields: Partial<Record<ProfileFieldName, string | string[]>>; company_verified: string | null };
export type LinkedRead = { record_id: string; linked: boolean; profile: PublicProfile | null; withdrawn: boolean };

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { cache: "no-store", ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = (body as { detail?: unknown }).detail;
    const why = typeof d === "string" ? d : (d as { refused?: string; tell_your_person?: string } | undefined)?.refused
      ?? (d as { tell_your_person?: string } | undefined)?.tell_your_person;
    throw new Error(why || `The server answered ${r.status}`);
  }
  return body as T;
}

/** Only an answer of the profile's shape is used (an older server, another page's mock: none). */
const asOwn = (x: OwnProfile): OwnProfile => {
  if (!x || typeof x !== "object" || !x.fields || typeof x.fields !== "object") throw new Error("Public profiles are not available here.");
  return x;
};
export const readOwnProfile = () => call<OwnProfile>("/api/user/profile").then(asOwn);
export const saveOwnProfile = (fields: Partial<Record<ProfileFieldName, OwnField>>, publish?: boolean) =>
  call<OwnProfile>("/api/user/profile", { method: "PUT", body: JSON.stringify({ fields, ...(publish === undefined ? {} : { publish }) }) }).then(asOwn);
export const withdrawOwnProfile = () => call<OwnProfile>("/api/user/profile/withdraw", { method: "POST" }).then(asOwn);
export const startCompanyVerification = (domain: string) =>
  call<{ domain: string; txt: string; how: string }>("/api/user/company-verification", { method: "POST", body: JSON.stringify({ domain }) });
export const checkCompanyVerification = () =>
  call<{ domain: string; verified_at?: number; method?: string }>("/api/user/company-verification/check", { method: "POST" });

export const searchDirectory = (q: string) =>
  call<{ profiles: PublicProfile[] }>(`/api/user/directory/profiles?q=${encodeURIComponent(q)}`).then((r) => r.profiles ?? []);

/** Open workspaces (lane 9's agent-api `GET /api/open?q=`, feature `open_workspaces`). */
export type OpenWorkspace = { slug: string; name: string; description?: string; members?: number; pages?: number;
  joining?: "direct" | "request"; member?: boolean };
export const searchOpenWorkspaces = (q: string) =>
  call<{ workspaces: OpenWorkspace[] }>(`/api/open?q=${encodeURIComponent(q)}`).then((r) => (Array.isArray(r.workspaces) ? r.workspaces : []));

async function crm<T>(operation: string, args: Record<string, unknown>): Promise<T> {
  return call<T>("/api/crm", { method: "POST", body: JSON.stringify({ operation, ...args }) });
}
export const readLinkedProfile = (record_id: string) => crm<LinkedRead>("profile/read", { record_id }).then((x) => {
  if (!x || typeof x !== "object" || typeof x.linked !== "boolean") throw new Error("Public profiles are not available here.");
  return x;
});
export const linkProfile = (record_id: string, handle: string, linked = true) =>
  crm<{ record_id: string; linked: boolean; handle: string | null }>("profile/link", { record_id, handle, linked });

export const first = (name: string) => name.trim().split(/\s+/)[0] || name;
export const show = (v: string | string[] | undefined) => (Array.isArray(v) ? v.join(" · ") : v ?? "");
