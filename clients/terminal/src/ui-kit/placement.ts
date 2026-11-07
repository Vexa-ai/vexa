/** A POPUP ALWAYS FULLY ON SCREEN (founder 2026-10-16: menus cut off by a panel edge or the top of
 *  the window). Rendered in a portal, it is placed from its trigger, then shifted or flipped so every
 *  edge stays inside the viewport. One rule for every menu (`Menu`, the model picker, the chip). */
import { useLayoutEffect, useState, type CSSProperties, type RefObject } from "react";

export type Placement = { align?: "start" | "end"; prefer?: "below" | "above" };

export function placeInViewport(trigger: DOMRect, box: { width: number; height: number }, vw: number, vh: number, p: Placement = {}): { top: number; left: number } {
  const M = 8, GAP = 4;
  let left = p.align === "end" ? trigger.right - box.width : trigger.left;
  left = Math.max(M, Math.min(left, vw - box.width - M));
  const below = trigger.bottom + GAP, above = trigger.top - GAP - box.height;
  let top = p.prefer === "above" ? above : below;
  if (top + box.height > vh - M && above >= M) top = above;
  if (top < M) top = below + box.height <= vh - M ? below : Math.max(M, vh - box.height - M);
  return { top, left };
}

export function useViewportPlacement(open: boolean, trigger: RefObject<HTMLElement | null>, box: RefObject<HTMLElement | null>, p: Placement = {}): CSSProperties {
  const [style, setStyle] = useState<CSSProperties>({ position: "fixed", top: 0, left: 0, visibility: "hidden" });
  useLayoutEffect(() => {
    if (!open) { setStyle({ position: "fixed", top: 0, left: 0, visibility: "hidden" }); return; }
    const t = trigger.current?.getBoundingClientRect(), b = box.current?.getBoundingClientRect();
    if (!t || !b) return;
    const vw = window.innerWidth || document.documentElement.clientWidth;
    const vh = window.innerHeight || document.documentElement.clientHeight;
    const at = placeInViewport(t, b, vw, vh, p);
    setStyle({ position: "fixed", top: at.top, left: at.left, right: "auto", bottom: "auto" });
  }, [open, p.align, p.prefer]);  // eslint-disable-line react-hooks/exhaustive-deps
  return style;
}
