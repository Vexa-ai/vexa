/** Profiles. The person's own profile is their CLAIMED CRM record (`/api/person/profile`, D35; one
 *  writer). Identity's R4 profile (`/api/user/profile*`) is retired: read-only for one release, its
 *  directory search (`/api/user/directory/*`) and a contact's link (`/api/crm` `profile/read`,
 *  `profile/link`) still read it until they move too. */

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
// ── ONE WRITER (PRD 0.13.4 D35, 2026-10-17) ─────────────────────────────────────────────────────
// The profile is the person's CLAIMED CRM record now (`/api/person/profile`); identity's profile
// above is retired (its writes answer 410). This editor keeps its shape and maps onto the claimed
// profile: name → display_name, role → headline, links → the LinkedIn/GitHub/X/website slots. Company
// verification stays identity's (read from its retired profile read for one release).
const TO_NEW: Partial<Record<ProfileFieldName, string>> = { name: "display_name", photo: "photo", role: "headline", company: "company", bio: "bio" };
const LINK_SLOTS = ["linkedin", "github", "x", "website"] as const;
type ClaimedProfile = { claimed: boolean; fields: Record<string, unknown>; public_fields: string[]; published: boolean; handle: string | null };

const personProfile = <T,>(body: object) => call<T>("/api/person/profile", { method: "POST", body: JSON.stringify(body) });

function toOwn(x: ClaimedProfile, verification: OwnProfile["company_verification"]): OwnProfile {
  if (!x || typeof x !== "object" || !x.fields) throw new Error("Profiles are not available here.");
  const pub = new Set(x.public_fields || []);
  const fields = {} as Record<ProfileFieldName, OwnField>;
  for (const k of PROFILE_FIELDS) {
    if (k === "links") {
      const l = (x.fields.links || {}) as Record<string, string>;
      fields.links = { value: LINK_SLOTS.map((s) => l[s]).filter(Boolean) as string[], public: pub.has("links") };
    } else {
      const n = TO_NEW[k]!;
      fields[k] = { value: String(x.fields[n] ?? ""), public: pub.has(n) };
    }
  }
  return { handle: x.published ? x.handle : null, published: !!x.published, linked_by: 0, fields, company_verification: verification };
}

function linksFor(urls: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const u of urls) {
    let host = "";
    try { host = new URL(u).hostname.toLowerCase(); } catch { continue; }
    const slot = host.endsWith("linkedin.com") ? "linkedin" : host.endsWith("github.com") ? "github"
      : (host.endsWith("x.com") || host.endsWith("twitter.com")) ? "x" : "website";
    if (!out[slot]) out[slot] = u;
  }
  return out;
}

async function verification(): Promise<OwnProfile["company_verification"]> {
  try { return (await call<{ company_verification?: OwnProfile["company_verification"] }>("/api/user/profile")).company_verification ?? null; }
  catch { return null; }
}

export const readOwnProfile = async () => toOwn(await personProfile<ClaimedProfile>({ action: "read" }), await verification());
export async function saveOwnProfile(fields: Partial<Record<ProfileFieldName, OwnField>>, publish?: boolean): Promise<OwnProfile> {
  const next: Record<string, unknown> = {};
  const pub: string[] = [];
  for (const [k, f] of Object.entries(fields) as [ProfileFieldName, OwnField][]) {
    if (!f) continue;
    if (k === "links") { next.links = linksFor(Array.isArray(f.value) ? f.value : [String(f.value)]); if (f.public) pub.push("links"); continue; }
    const n = TO_NEW[k]; if (!n) continue;
    next[n] = Array.isArray(f.value) ? f.value[0] ?? "" : f.value;
    if (f.public) pub.push(n);
  }
  await personProfile({ action: "edit", fields: next });
  if (publish !== undefined) await personProfile({ action: "publish", public: publish, public_fields: pub });
  return readOwnProfile();
}
export const withdrawOwnProfile = async () => { await personProfile({ action: "publish", public: false }); return readOwnProfile(); };
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
