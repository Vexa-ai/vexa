/** WHO "YOU" ARE (feature `shell2`; founder 2026-10-17: "your own references read 'you'"). The shell
 *  sets the signed-in person once; every name drawn by the shell2 surfaces goes through `nameOrYou`. */
let me: { name: string; email: string; subject: string } = { name: "", email: "", subject: "" };

export function setMe(m: { name?: string | null; email?: string | null; subject?: string | null }): void {
  me = { name: (m.name ?? "").trim(), email: (m.email ?? "").trim().toLowerCase(), subject: (m.subject ?? "").trim() };
}
export function meNow(): { name: string; email: string; subject: string } { return me; }

/** Is this name, address or account the signed-in person? */
export function isMe(who: { name?: string | null; email?: string | null; subject?: string | null } | string | null | undefined): boolean {
  if (!who) return false;
  const w = typeof who === "string" ? { name: who, email: who.includes("@") ? who : "" } : who;
  if (w.subject && me.subject && w.subject === me.subject) return true;
  if (w.email && me.email && w.email.trim().toLowerCase() === me.email) return true;
  const n = (w.name ?? "").trim().toLowerCase();
  return !!n && !!me.name && n === me.name.toLowerCase();
}

/** A person as the reader reads them: "you" for yourself, their name otherwise. */
export const nameOrYou = (name: string, email?: string): string => (isMe({ name, email }) ? "you" : name);
/** First name for compact lines ("with Ines, Dan +2"); "you" for yourself. */
export const firstOrYou = (name: string, email?: string): string => (isMe({ name, email }) ? "you" : (name.trim().split(/\s+/)[0] || name));
