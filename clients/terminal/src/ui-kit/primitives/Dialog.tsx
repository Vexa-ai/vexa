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
  // FOCUS MOVES IN ONCE, WHEN THE DIALOG OPENS — never on a re-render. `onClose` is nearly always an
  // inline arrow, so with it in the effect's dependencies every parent render (the rail re-renders
  // on each `GET /api/sessions` poll, every ~5 s) ran the cleanup and the setup again: focus went
  // back to the trigger and then to the dialog's FIRST control. A reader who had tabbed to "Delete
  // chat" was moved back to "Cancel" under their hands, and Enter cancelled — the delete never went
  // out (founder, app.dev 2026-10-11: "delete still does not work for chats"). The latest `onClose`
  // is read through a ref instead.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const before = document.activeElement as HTMLElement | null;
    const el = box.current;
    (el?.querySelector<HTMLElement>("[data-autofocus]") ?? el?.querySelector<HTMLElement>(FOCUSABLE) ?? el)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); closeRef.current(); return; }
      if (e.key !== "Tab" || !el) return;
      const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!items.length) { e.preventDefault(); return; }
      const a = items[0], z = items[items.length - 1];
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); if (before && document.contains(before)) before.focus(); };
  }, [open]);
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

export function ConfirmDialog({ open, onCancel, onConfirm, title, consequence, confirmLabel, tone = "danger", typeToConfirm, busy, focusConfirm }: {
  open: boolean; onCancel: () => void; onConfirm: () => void; title: string; consequence: ReactNode;
  /** Focus the named act instead of Cancel, so Enter confirms. For a dialog the reader opened by
   *  choosing that very act ("Delete…" in a row's menu, or its shortcut): the dialog is the second
   *  step, and Enter answering it with Cancel read as "delete does nothing". Esc still cancels.
   *  Ignored with `typeToConfirm`, whose field takes focus. */
  focusConfirm?: boolean;
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
      <Button variant={tone} onClick={onConfirm} disabled={!ready || busy} loading={busy} data-autofocus={focusConfirm && !typeToConfirm ? "" : undefined}>{confirmLabel}</Button>
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

export type ConfirmRequest = { title: string; consequence: ReactNode; confirmLabel: string; tone?: "danger" | "primary"; typeToConfirm?: string };

/** `useConfirm()` — the drop-in for `window.confirm` (guidelines §4.11, S7): `await confirm({…})`
 *  resolves true on the named confirm button, false on Cancel, Escape or the scrim. Render the
 *  returned `dialog` once in the component. One request at a time; a new one cancels the last. */
export function useConfirm(): [(req: ConfirmRequest) => Promise<boolean>, ReactNode] {
  const [req, setReq] = useState<(ConfirmRequest & { resolve: (ok: boolean) => void }) | null>(null);
  const confirm = (r: ConfirmRequest) => new Promise<boolean>((resolve) => {
    setReq((prev) => { prev?.resolve(false); return { ...r, resolve }; });
  });
  const close = (ok: boolean) => { req?.resolve(ok); setReq(null); };
  const dialog = (
    <ConfirmDialog open={!!req} title={req?.title ?? ""} consequence={req?.consequence} confirmLabel={req?.confirmLabel ?? "Confirm"}
      tone={req?.tone} typeToConfirm={req?.typeToConfirm} onCancel={() => close(false)} onConfirm={() => close(true)} />
  );
  return [confirm, dialog];
}
