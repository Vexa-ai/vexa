"use client";
/** Sheet (guidelines §3.1, §4.18) — the overlay form of a side pane.
 *
 *  ONE ELEMENT, THREE FORMS, so a pane keeps its state when the window crosses a breakpoint:
 *    · `inline`     — `display: contents`; the children sit in the shell's grid as if the sheet
 *                     were not there (the docked pane).
 *    · `overlay`    — slides over the conversation from its side, with a scrim; Esc, a scrim click
 *                     or the pane's own close control dismisses it.
 *    · `fullscreen` — the same, covering the shell (the single-column mode).
 *  The element never changes type or position in the tree, so React never remounts the pane: a
 *  half-written edit in the pages panel survives the window being narrowed.
 *
 *  An open overlay is a modal dialog: focus moves in, Tab is trapped, and on close focus returns to
 *  whatever had it before (the control that opened it). A CLOSED overlay stays mounted but `inert`
 *  and hidden, for the same keep-the-state reason. Motion is the slide in `.vx-sheet`, which falls
 *  back to a fade under `prefers-reduced-motion`. */
import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";

export type SheetForm = "inline" | "overlay" | "fullscreen";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Sheet({ form, open, onClose, side = "right", width, label, children, dataPane }: {
  form: SheetForm; open: boolean; onClose: () => void; side?: "left" | "right";
  /** Overlay width in px (ignored inline and fullscreen). */
  width?: number; label: string; children: ReactNode;
  /** `data-pane` on the element — what tests and the layout sweep select on. */
  dataPane?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const overlay = form !== "inline";
  const shown = overlay && open;

  useEffect(() => {
    if (!shown) return;
    const el = box.current;
    const before = document.activeElement as HTMLElement | null;
    // Move focus in: the first control, else the sheet itself — on the next frame, once the open
    // state has painted and the sheet is visible (an element that is still hidden takes no focus).
    const frame = requestAnimationFrame(() => {
      const first = el?.querySelector<HTMLElement>(FOCUSABLE);
      (first ?? el)?.focus();
    });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
      if (e.key !== "Tab" || !el) return;
      const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((n) => n.offsetParent !== null || n === document.activeElement);
      if (items.length === 0) { e.preventDefault(); el.focus(); return; }
      const a = items[0], z = items[items.length - 1];
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKey);
      // Return focus to the trigger, if it is still on the page.
      if (before && document.contains(before)) before.focus();
    };
  }, [shown, onClose]);

  const style: CSSProperties | undefined = form === "overlay" && width ? { width } : undefined;
  return (
    <>
      {shown && <div className="vx-scrim" aria-hidden onClick={onClose} />}
      <div ref={box} className="vx-sheet" data-form={form} data-side={side} data-open={shown ? "" : undefined}
        data-pane={dataPane}
        role={overlay ? "dialog" : undefined} aria-modal={shown || undefined} aria-label={overlay ? label : undefined}
        aria-hidden={overlay && !open ? true : undefined}
        tabIndex={overlay ? -1 : undefined}
        // `inert` keeps a closed overlay out of the tab order and the accessibility tree while it
        // stays mounted. React 19 types it as a boolean attribute.
        inert={overlay && !open ? true : undefined}
        style={style}>
        {children}
      </div>
    </>
  );
}
