/** `/decide/<card>` — the pure half of the nudge landing (0.13.4 S6). The page reads a card through
 *  the desktop card proxy and shows it alone; these functions turn whatever crm-api answered into
 *  what the page draws, tolerating the fields S5 may or may not send yet (`actions`, `kind`,
 *  `fields`, `next_actor`, `from`, `workspace_name`). */

export type DecideAction = { id: string; label: string; transition?: string; tone?: string };
export type DecideCard = {
  id: string;
  revision: number;
  state: string;
  title: string;
  kind: string;
  fields: { label: string; value: string }[];
  suggestion: string;
  actions: DecideAction[];
  workspace: string;
  from: string;
  at: number | null;
  href: string;
};

type Raw = Record<string, unknown>;
const str = (v: unknown): string => (typeof v === "string" ? v.trim() : typeof v === "number" ? String(v) : "");
const KIND: Record<string, string> = { commitment: "Commitment", approval: "Approval", invitation: "Invitation", decision: "Decision", task: "Task" };
const CLOSED = new Set(["done", "declined", "closed", "cancelled", "canceled", "archived", "signed", "approved", "rejected"]);

/** The card out of a `/card/read` answer (`{card: …}` or the card itself), or null. */
export function cardFrom(body: unknown): Raw | null {
  const b = (body && typeof body === "object" ? body : null) as Raw | null;
  if (!b) return null;
  const c = (b.card && typeof b.card === "object" ? b.card : b) as Raw;
  return typeof c.id === "string" && c.id ? c : null;
}

function timeOf(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v < 1e12 ? v * 1000 : v;
  if (typeof v === "string" && v) { const t = Date.parse(v); return Number.isFinite(t) ? t : null; }
  return null;
}

/** Only a same-origin path is followed (never `//host` or a scheme). */
export const safePath = (h: string): string => (h.startsWith("/") && !h.startsWith("//") ? h : "");

/** The decisions this viewer can take: crm's own `actions` when it sends them; otherwise the
 *  stages it declares (the next one, then the side states), and only when it says this viewer may
 *  move the stage. */
export function actionsOf(c: Raw): DecideAction[] {
  if (Array.isArray(c.actions)) {
    return (c.actions as unknown[]).flatMap((a) => {
      const o = (a && typeof a === "object" ? a : {}) as Raw;
      const id = str(o.id) || str(o.transition) || str(o.label);
      const label = str(o.label) || str(o.transition) || id;
      if (!id || !label) return [];
      return [{ id, label, ...(str(o.transition) ? { transition: str(o.transition) } : {}), ...(str(o.tone) ? { tone: str(o.tone) } : {}) }];
    });
  }
  const can = (c.can && typeof c.can === "object" ? c.can : {}) as Raw;
  if (can.transition !== true) return [];
  const states = Array.isArray(c.states) ? (c.states as unknown[]).map(str).filter(Boolean) : [];
  const side = Array.isArray(c.side_states) ? (c.side_states as unknown[]).map(str).filter(Boolean) : [];
  const i = states.indexOf(str(c.state));
  const next = i >= 0 && i + 1 < states.length ? [states[i + 1]] : [];
  return [...next, ...side].filter((s) => s !== str(c.state)).map((s) => ({ id: s, label: s.charAt(0).toUpperCase() + s.slice(1), transition: s }));
}

export function viewOf(c: Raw): DecideCard {
  const f = (c.fields && typeof c.fields === "object" ? c.fields : {}) as Raw;
  const wsNames = Array.isArray(c.workspaces) ? (c.workspaces as unknown[]).map(str).filter(Boolean) : [];
  const workspace = str(c.workspace_name) || wsNames[0] || "";
  const who = str(f.who);
  const what = str(f.what) || str(c.summary);
  const date = str(f.date) || str(c.due);
  const visible = str(f.visibility) || str(f.visible_to) || (workspace ? `${workspace} members` : "");
  const fields = ([["Who", who], ["What", what], ["Date", date], ["Visible to", visible]] as const)
    .filter(([, v]) => v).map(([label, value]) => ({ label, value }));
  const rawKind = str(c.kind) || str(c.type);
  const sug = c.suggestion ?? c.rule;
  const suggestion = typeof sug === "string" ? sug.trim() : str((sug as Raw | undefined)?.text);
  const movement = (c.movement && typeof c.movement === "object" ? c.movement : {}) as Raw;
  return {
    id: str(c.id),
    revision: typeof c.revision === "number" ? c.revision : Number(c.revision) || 0,
    state: str(c.state),
    title: str(c.title) || "Untitled card",
    kind: KIND[rawKind.toLowerCase()] ?? (rawKind ? rawKind.charAt(0).toUpperCase() + rawKind.slice(1) : ""),
    fields,
    suggestion,
    actions: actionsOf(c),
    workspace,
    from: str(c.from) || str(movement.by),
    at: timeOf(c.created_at ?? c.at ?? movement.at),
    href: safePath(str(c.href)),
  };
}

/** The cards still waiting on this person after `current`, from `/card/list {mine: true}`.
 *  `mine` is the server's filter; this only drops the one just decided and any that are closed,
 *  and — when the server names a next actor and we know who we are — any that wait on someone else. */
export function waitingAfter(body: unknown, current: string, me?: { id?: string; email?: string }): { id: string; title: string }[] {
  const b = body as Raw | unknown[] | null;
  const list = Array.isArray(b) ? b : Array.isArray((b as Raw | null)?.cards) ? ((b as Raw).cards as unknown[])
    : Array.isArray((b as Raw | null)?.items) ? ((b as Raw).items as unknown[]) : [];
  const mine = new Set([me?.id, me?.email, "me", "you"].filter(Boolean) as string[]);
  return list.flatMap((x) => {
    const c = (x && typeof x === "object" ? x : {}) as Raw;
    const id = str(c.id);
    if (!id || id === current) return [];
    if (c.open === false || c.done === true || CLOSED.has(str(c.state).toLowerCase())) return [];
    const actor = c.next_actor;
    const actorId = typeof actor === "string" ? actor : str((actor as Raw | undefined)?.id) || str((actor as Raw | undefined)?.subject);
    if (actorId && (me?.id || me?.email) && !mine.has(actorId) && (actor as Raw | undefined)?.you !== true) return [];
    return [{ id, title: str(c.title) || "Untitled card" }];
  });
}

/** "just now", "5 min ago", "14 h ago", "yesterday", then a date. */
export function ago(ms: number | null, now = Date.now()): string {
  if (ms === null) return "";
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 2 * 86400) return "yesterday";
  try { return new Date(ms).toLocaleDateString(undefined, { day: "numeric", month: "short" }); } catch { return ""; }
}

/** One line for a refused decision. Never a code. */
export function refusedLine(status: number): string {
  if (status === 409) return "The card changed elsewhere. It has been reloaded.";
  if (status === 403) return "You can't decide this card.";
  if (status === 404) return "This card is no longer here.";
  return "That didn't go through just now. Try again.";
}

/** R11: a destructive decision (decline, reject, cancel…) is never final on the first tap — the
 *  Done view offers Undo, which moves the card back to the stage it was in. */
export const isDestructive = (a: { id: string; transition?: string }): boolean =>
  /declin|reject|cancel|withdraw|delet|archiv|remove/i.test(`${a.transition ?? ""} ${a.id}`);

export const decidePath = (id: string): string => `/decide/${encodeURIComponent(id)}`;
export const redeemHref = (t: string, id: string): string =>
  `/api/auth/redeem?t=${encodeURIComponent(t)}&next=${encodeURIComponent(decidePath(id))}`;
