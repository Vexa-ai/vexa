/** "Add to desktop" / "Remove from desktop" for a routine's, a chat's and a file's "…" (#1783, UI
 *  decisions 15e; desktop ADR Q1: placements, not cards), and the drag a sidebar row carries onto
 *  the desktop. The item writes `place` / `remove` through the same `arrange` every other desktop
 *  change uses, so it has Undo.
 *
 *  OFFERED WHENEVER THE `desktop` FEATURE IS ON (founder 2026-10-15: "this just does not exist").
 *  It used to wait for the desktop to have been read, so a menu opened before that read — or after
 *  one that failed — had no item at all. Until the read answers the label is "Add to desktop":
 *  placing is idempotent on the server, so the worst case is a no-op with its own toast. A file on
 *  the person's own desk is addressed by their subject id, which is resolved at the press when it is
 *  not known yet. */
import { useEffect, useState, type DragEvent } from "react";
import { useFeature } from "../../platform/features";
import type { ContextMenuItem } from "../../ui-kit/ContextMenu";
import { arrange, ensureSubject, knownSubject, useDesktopState } from "./desktopApi";

/** A routine (its flow name), a chat (its session id) or a file (`slug` absent = the person's own desk). */
export type DesktopTarget =
  | { kind: "agent"; id: string }
  | { kind: "chat"; id: string }
  | { kind: "file"; slug?: string | null; path: string };

/** The drag payload a sidebar row carries onto the desktop (a `DesktopTarget`, as JSON). */
export const DESKTOP_DRAG_TYPE = "application/x-vexa-desktop-target";

const DESK_ALIASES = new Set(["personal", "desk"]);

export function refFor(t: DesktopTarget, deskSlug: string | null): string | null {
  if (t.kind === "agent") return t.id ? `agent:${t.id}` : null;
  if (t.kind === "chat") return t.id ? `chat:${t.id}` : null;
  const own = !t.slug || DESK_ALIASES.has(t.slug);
  const slug = own ? deskSlug : t.slug;
  const path = t.path.replace(/^\/+/, "");
  return slug && path ? `file:${slug}/${path}` : null;
}

/** The section a target is drawn in — a drop onto any other section is not a placement. */
export const sectionForTarget = (t: DesktopTarget): "agents" | "files" => (t.kind === "file" ? "files" : "agents");

/** Place a target, resolving the person's own desk slug first when it is needed and not known. */
export async function placeTarget(t: DesktopTarget): Promise<boolean> {
  const desk = knownSubject() ?? (t.kind === "file" ? await ensureSubject() : null);
  const ref = refFor(t, desk);
  return ref ? arrange([{ op: "place", ref }]) : false;
}

export function writeDesktopDrag(e: DragEvent<HTMLElement>, t: DesktopTarget): void {
  try { e.dataTransfer?.setData(DESKTOP_DRAG_TYPE, JSON.stringify(t)); } catch { /* a browser that refuses a custom type */ }
}

export function readDesktopDrag(e: DragEvent<HTMLElement>): DesktopTarget | null {
  try {
    const t = JSON.parse(e.dataTransfer?.getData(DESKTOP_DRAG_TYPE) || "null") as DesktopTarget | null;
    if (!t || typeof t !== "object") return null;
    if ((t.kind === "agent" || t.kind === "chat") && typeof t.id === "string" && t.id) return t;
    if (t.kind === "file" && typeof t.path === "string" && t.path) return t;
    return null;
  } catch { return null; }
}

/** Is the desktop switched on — the sidebar rows carry a drag payload only then. */
export function useDesktopOn(): boolean {
  return useFeature("desktop");
}

export function useDesktopItems(): (t: DesktopTarget) => ContextMenuItem[] {
  const on = useFeature("desktop");
  const { phase, desktop } = useDesktopState(on);
  const [desk, setDesk] = useState<string | null>(knownSubject());
  useEffect(() => { if (on) void ensureSubject().then(setDesk); }, [on]);
  return (t) => {
    if (!on) return [];
    if (t.kind !== "file" && !t.id) return [];
    if (t.kind === "file" && !t.path) return [];
    const ref = refFor(t, desk);
    const placed = !!ref && phase === "ready" && !!desktop && desktop.sections.some((s) => s.items.some((i) => i.ref === ref));
    return [placed
      ? { id: "desktop-remove", label: "Remove from desktop", onSelect: () => { void arrange([{ op: "remove", ref: ref! }]); } }
      : { id: "desktop-add", label: "Add to desktop", onSelect: () => { void placeTarget(t); } }];
  };
}
