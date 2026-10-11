"use client";
/** Dialog and ConfirmDialog (guidelines §4.11). A modal: focus moves in, Tab is trapped, Esc
 *  closes, focus returns to the trigger, the title labels the dialog. ConfirmDialog replaces
 *  `window.confirm`: its confirm button NAMES THE ACT ("Delete chat", never "OK"), and a fully
 *  irreversible act can require typing a name before it enables. */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Button } from "./Button";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Dialog({ open, onClose, title, children, footer, width = "form" }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode; width?: "confirm" | "form" | "wide";
}) {
  const box = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const before = document.activeElement as HTMLElement | null;
    const el = box.current;
    (el?.querySelector<HTMLElement>("[data-autofocus]") ?? el?.querySelector<HTMLElement>(FOCUSABLE) ?? el)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
      if (e.key !== "Tab" || !el) return;
      const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!items.length) { e.preventDefault(); return; }
      const a = items[0], z = items[items.length - 1];
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); if (before && document.contains(before)) before.focus(); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="vx-dialog-layer">
      <div className="vx-scrim" aria-hidden onClick={onClose} />
      <div ref={box} role="dialog" aria-modal="true" aria-labelledby={id} tabIndex={-1} className="vx-dialog" data-width={width}>
        <h2 id={id} className="vx-dialog-title">{title}</h2>
        <div className="vx-dialog-body">{children}</div>
        {footer && <div className="vx-dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function ConfirmDialog({ open, onCancel, onConfirm, title, consequence, confirmLabel, tone = "danger", typeToConfirm, busy }: {
  open: boolean; onCancel: () => void; onConfirm: () => void; title: string; consequence: ReactNode;
  /** The act, named: "Delete chat". */
  confirmLabel: string; tone?: "danger" | "primary";
  /** For a fully irreversible act: the exact text the reader must type first. */
  typeToConfirm?: string; busy?: boolean;
}) {
  const [typed, setTyped] = useState("");
  useEffect(() => { if (!open) setTyped(""); }, [open]);
  const ready = !typeToConfirm || typed === typeToConfirm;
  return (
    <Dialog open={open} onClose={onCancel} title={title} width="confirm" footer={<>
      <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      <Button variant={tone} onClick={onConfirm} disabled={!ready || busy} loading={busy}>{confirmLabel}</Button>
    </>}>
      <div className="vx-dialog-text">{consequence}</div>
      {typeToConfirm && (
        <label className="vx-field">
          <span className="vx-field-label">Type <strong>{typeToConfirm}</strong> to confirm</span>
          <input className="vx-input" value={typed} onChange={(e) => setTyped(e.target.value)} data-autofocus autoComplete="off" />
        </label>
      )}
    </Dialog>
  );
}
