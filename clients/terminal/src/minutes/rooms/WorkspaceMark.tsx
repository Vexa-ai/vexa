"use client";
/** A workspace's mark: its monogram on its colour (one of eight hues, derived from the workspace id,
 *  the same on every load). Stands in until workspace images land (workspace-identity); the shape —
 *  a rounded square — already says "workspace". Sizes are classes, colours are tokens in rooms.css. */
import "./rooms.css";

export function hueOf(id: string): number {
  let h = 7;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return h % 8;
}
const monogram = (name: string) => (name.match(/\b[\p{L}\p{N}]/gu) ?? []).slice(0, 2).join("").toUpperCase() || "?";
const SIZES = [16, 30, 32, 40] as const;

export function WorkspaceMark({ id, name, size = 32 }: { id: string; name: string; size?: 16 | 30 | 32 | 40 }) {
  const s = SIZES.includes(size) ? size : 32;
  return <span className="vx-wm" data-h={hueOf(id || name)} data-size={s} aria-hidden>{monogram(name)}</span>;
}
