"use client";
/** COPY REFERENCE AND SHARE ON EVERY OBJECT (UI 7gg, 7ff; PRD 0.13.4 D18/D19).
 *
 *  `objectActions(target)` is the one list of menu items every object's ⋯ carries: Copy reference
 *  (reference + URL on the clipboard), Share (single objects only — "Send to…"; on a phone the native
 *  share sheet), Pin for everyone (7ee) where the object sits in a workspace or room. Lists get
 *  `listActions` — Copy link only: the recipient sees their own version of the list.
 *  `ObjectMenu` is the ⋯ button; `useLongPress` opens the same sheet on touch (phone pass);
 *  ⌘⇧C copies the reference of the object under focus or hover. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { MoreHorizontal } from "lucide-react";
import { Menu, type MenuItem } from "../../ui-kit";
import { toast } from "../../ui-kit/primitives/Toast";
import { refChip } from "../../ui-kit/universalRefs";
import { copyReference, MESSAGE_ACTIONS_EVENT, nativeShare, type RefTarget } from "../../surfaces/reference";
import { ShareDialog } from "./ShareDialog";

/** Kinds Share applies to: single objects (the coordinator's simplification). */
export const SHAREABLE = new Set(["meeting", "page", "file", "entity", "card", "room", "chat", "message"]);
export const isTouch = (): boolean => typeof window !== "undefined" && (window.matchMedia?.("(pointer: coarse)").matches ?? false);

export async function pin(target: RefTarget, where: { workspace: string; room?: string }, pinned = true): Promise<boolean> {
  try {
    const r = await fetch("/api/pins", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace: where.workspace, ...(where.room ? { room: where.room } : {}), ref: refChip(target.kind, target.id, undefined, target.anchor), pinned }) });
    toast(r.ok ? (pinned ? "Pinned for everyone here" : "Unpinned") : r.status === 403 ? "Only contributors can pin here" : "Couldn't pin it just now");
    if (r.ok) window.dispatchEvent(new CustomEvent(PINS_CHANGED_EVENT, { detail: where }));
    return r.ok;
  } catch { toast("Couldn't pin it just now"); return false; }
}
export const PINS_CHANGED_EVENT = "vexa:pins-changed";

export function objectActions(target: RefTarget, opts: { onShare: () => void; workspace?: string; room?: string }): MenuItem[] {
  const items: MenuItem[] = [{ key: "copy-ref", label: "Copy reference", onSelect: () => { void copyReference(target); } }];
  if (SHAREABLE.has(target.kind)) {
    items.push({ key: "share", label: "Share…", onSelect: () => {
      if (isTouch()) { void nativeShare(target).then((ok) => { if (!ok) opts.onShare(); }); } else opts.onShare();
    } });
  }
  if (opts.workspace && opts.workspace !== "personal" && SHAREABLE.has(target.kind)) {
    items.push({ key: "pin", label: "Pin for everyone", onSelect: () => { void pin(target, { workspace: opts.workspace!, room: opts.room }); } });
  }
  return items;
}

/** A list's ⋯: Copy link only (the recipient opens their own version of the list). */
export function listActions(query: string): MenuItem[] {
  return [{ key: "copy-link", label: "Copy link", onSelect: () => { void copyReference({ kind: "query", id: query }); } }];
}

export function ObjectMenu({ target, workspace, room }: { target: RefTarget; workspace?: string; room?: string }) {
  const [sharing, setSharing] = useState(false);
  return <>
    <Menu label={`More for ${target.label ?? target.kind}`} variant="ghost" align="end" trigger={<MoreHorizontal size={14} aria-hidden />}
      items={objectActions(target, { onShare: () => setSharing(true), workspace, room })} />
    {sharing && <ShareDialog object={target} onClose={() => setSharing(false)} />}
  </>;
}

/** Long-press (touch) opens the object's sheet; returns props to spread on the element. */
export function useLongPress(onLong: () => void, ms = 500) {
  const t = useRef<number | null>(null);
  const clear = useCallback(() => { if (t.current !== null) { window.clearTimeout(t.current); t.current = null; } }, []);
  useEffect(() => clear, [clear]);
  return {
    onTouchStart: () => { clear(); t.current = window.setTimeout(() => { t.current = null; onLong(); }, ms); },
    onTouchEnd: clear, onTouchMove: clear, onTouchCancel: clear,
    onContextMenu: (e: { preventDefault: () => void }) => { if (isTouch()) e.preventDefault(); },
  };
}

/** ⌘⇧C (Ctrl⇧C) copies the reference of the object this wrapper holds while it has focus or hover. */
export function CopyRefScope({ target, children, className }: { target: RefTarget; children: ReactNode; className?: string }) {
  const hover = useRef(false);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (!hover.current || !(e.metaKey || e.ctrlKey) || !e.shiftKey || e.key.toLowerCase() !== "c") return;
      e.preventDefault();
      void copyReference(target);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [target]);
  return <div className={className} onMouseEnter={() => { hover.current = true; }} onMouseLeave={() => { hover.current = false; }}
    onFocus={() => { hover.current = true; }} onBlur={() => { hover.current = false; }}>{children}</div>;
}

/** The phone sheet for a message: Copy reference / Share (the native sheet when there is one). */
export function MessageSheet({ target, onClose }: { target: RefTarget; onClose: () => void }) {
  const [sharing, setSharing] = useState(false);
  if (sharing) return <ShareDialog object={target} onClose={onClose} />;
  return (<>
    <div className="vx-rd-scrim" aria-hidden onClick={onClose} />
    <div className="vx-rd-sheet" role="dialog" aria-label="Message actions" data-message-sheet="">
      <button type="button" onClick={() => { void copyReference(target).then(onClose); }}>Copy reference</button>
      <button type="button" onClick={() => { void nativeShare(target).then((ok) => { if (ok) onClose(); else setSharing(true); }); }}>Share…</button>
      <button type="button" onClick={onClose}>Cancel</button>
    </div>
  </>);
}

/** Listens for a chat's long-press (`MESSAGE_ACTIONS_EVENT`) and shows the message sheet. Mounted once by the shell. */
export function MessageActionsHost() {
  const [target, setTarget] = useState<RefTarget | null>(null);
  useEffect(() => {
    const on = (e: Event) => setTarget((e as CustomEvent<RefTarget>).detail ?? null);
    window.addEventListener(MESSAGE_ACTIONS_EVENT, on);
    return () => window.removeEventListener(MESSAGE_ACTIONS_EVENT, on);
  }, []);
  return target ? <MessageSheet target={target} onClose={() => setTarget(null)} /> : null;
}
