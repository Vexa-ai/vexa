"use client";
/** Your face in the personal chat's header (R1, `rooms`): a chat is yours alone, so its identity is
 *  you. Initials until the photo route lands here. Plus the link a "Think privately" chat keeps to
 *  the room it is about (this browser's record; the chat itself is an ordinary private chat). */
import "./rooms.css";

const initials = (name: string) => {
  const base = name.includes("@") ? name.split("@")[0] : name;
  return (base.match(/\b[\p{L}\p{N}]/gu) ?? []).slice(0, 2).join("").toUpperCase() || "·";
};

export function Face({ name }: { name: string }) {
  return <span className="vx-face" title={name || "You"} aria-label={name ? `You (${name})` : "You"}>{initials(name)}</span>;
}

const KEY = "vexa.rooms.thinkOf";
type Link = { session: string; label: string };
function read(): Record<string, Link> { try { return JSON.parse(localStorage.getItem(KEY) || "{}") as Record<string, Link>; } catch { return {}; } }
export function rememberThink(chatId: string, link: Link): void {
  try { localStorage.setItem(KEY, JSON.stringify({ ...read(), [chatId]: link })); } catch { /* storage off: the link is a convenience */ }
}
export function thinkOf(chatId: string): Link | null { return read()[chatId] ?? null; }
