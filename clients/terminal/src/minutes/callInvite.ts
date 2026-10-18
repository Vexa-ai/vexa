/** COPY INVITE LINK FOR A CALL (founder 2026-10-16). The link is the call's CHAT join link — the
 *  existing join-link door (`POST /api/join-links`, kind `chat`): scoped to this chat (the call is
 *  in it), expiring in a day, revocable with "Stop link". A signed-in person who opens it joins the
 *  chat, and from it the call. (A guest with no account cannot join through it yet.) */
import { getJson } from "../data/apiClient";

let current: { session: string; id: string } | null = null;

export async function copyCallInvite(session: string, copy: (t: string) => Promise<void> = (t) => navigator.clipboard.writeText(t)): Promise<"copied" | "failed"> {
  try {
    const r = await getJson<{ id: string; url?: string | null }>("/api/join-links", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_kind: "chat", target_id: session, role: "write", max_uses: 50, expires_in_days: 1 }),
    });
    if (!r?.url) return "failed";
    current = { session, id: r.id };
    await copy(r.url);
    return "copied";
  } catch { return "failed"; }
}

export function hasCallInvite(session: string): boolean { return current?.session === session; }

export async function stopCallInvite(session: string): Promise<boolean> {
  if (!current || current.session !== session) return false;
  try {
    await getJson("/api/join-links/revoke", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_kind: "chat", target_id: session, link_id: current.id }) });
    current = null;
    return true;
  } catch { return false; }
}

/** Test seam. */
export function resetCallInvite(): void { current = null; }
