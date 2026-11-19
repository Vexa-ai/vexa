/** personApi — the signed-in person's own Person (PRD 0.13.4 D35: one Person, many addresses).
 *
 *  There is no persons table: the human is the CRM's Person record, and each login resolves to it by
 *  the address it signed in with. When one human has several logins, a *merge card* asks them to
 *  confirm, from each login, that the accounts are all theirs, and which address is primary (where
 *  invites and reminders go). Nothing merges without that. */

export interface MergeOption { option: string; address: string; yours: boolean }
export interface MergeCard {
  id: string; status: "merge_offered" | "merge_confirmed" | "merge_declined";
  accounts: number; confirmed_by_you: boolean; waiting_on: number;
  /** your own address in full, any other masked (`v•••@example.org`) */
  addresses: MergeOption[];
  primary: string | null;
}
export interface PersonMe {
  person: string | null; accounts: number; addresses: string[]; primary: string | null; merge: MergeCard | null;
}

async function post<T>(op: "me" | "merge" | "profile", body: object): Promise<T> {
  const r = await fetch(`/api/person/${op}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), cache: "no-store",
  });
  const data = (await r.json().catch(() => ({}))) as T & { detail?: unknown };
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${r.status}`);
  return data;
}

export const readMe = () => post<PersonMe>("me", {});
export const confirmMerge = (card: string, primary?: string) =>
  post<{ status: "confirmed" | "merged"; merge: MergeCard }>("merge", { action: "confirm", card, ...(primary ? { primary } : {}) });
export const declineMerge = (card: string) => post<{ status: "declined"; split: number }>("merge", { action: "decline", card });

/** After the last login confirmed: each account gets the workspaces any of them is in (agent-api,
 *  a person's own act; it does nothing unless the CRM says the person merged them). */
export async function openWorkspacesToEveryLogin(): Promise<void> {
  await fetch("/api/people/me/accounts-merged", { method: "POST", cache: "no-store" }).catch(() => undefined);
}

/** What the card says, in one sentence, for the state it is in. */
export function mergeSentence(card: MergeCard): string {
  if (card.confirmed_by_you && card.waiting_on > 0) {
    return card.waiting_on === 1
      ? "Confirmed here. Sign in with your other address and confirm there to finish."
      : `Confirmed here. Sign in with each of your other ${card.waiting_on} addresses and confirm there to finish.`;
  }
  return `You sign in to Vexa with ${card.accounts} addresses. Are these accounts all yours?`;
}

/** A profile is the person's CLAIMED CRM record (D35, founder addition): these fields are theirs
 *  alone; the org's fields on the same record never appear here. */
export interface ProfileFields {
  display_name?: string; photo?: string; headline?: string; company?: string; bio?: string;
  location?: string; timezone?: string;
  links?: { linkedin?: string; github?: string; x?: string; website?: string };
  contact?: { prefer?: "email" | "chat" | "call"; note?: string };
}
export interface MyProfile {
  claimed: boolean; via_merge?: boolean; record_id: string | null; fields: ProfileFields;
  public_fields: (keyof ProfileFields)[]; published: boolean; handle: string | null;
  /** several records match your address: pick yours (the claim card) */
  candidates: { record_id: string; name: string }[];
}
export const readProfile = () => post<MyProfile>("profile", { action: "read" });
export const editProfile = (fields: ProfileFields) => post<{ status: "saved"; fields: ProfileFields }>("profile", { action: "edit", fields });
export const claimProfile = (record_id?: string) => post<{ status: "claimed" | "choose" }>("profile", { action: "claim", ...(record_id ? { record_id } : {}) });
export const unclaimProfile = () => post<{ status: "unclaimed" }>("profile", { action: "unclaim" });
export const publishProfile = (on: boolean, public_fields?: (keyof ProfileFields)[]) =>
  post<{ published: boolean; handle: string | null }>("profile", { action: "publish", public: on, ...(public_fields ? { public_fields } : {}) });

/** The public page's path for a handle. */
export const publicProfilePath = (handle: string) => `/p/${encodeURIComponent(handle)}`;
