"use client";
/** EmptyState, Skeleton, ErrorState, Tooltip, Popover (guidelines §4.13–4.15, §5.4).
 *  Every async region has four designed states — loading, empty, error, content — and "nothing
 *  here" never looks like "could not load" (P18). ErrorState renders a typed fault: what failed,
 *  why, the fix, and the verbatim fault behind a "Details" disclosure. A Skeleton appears only
 *  after 300ms. A Tooltip is supplementary: its trigger keeps its own accessible name. */
import { cloneElement, isValidElement, useEffect, useId, useRef, useState, type ReactElement, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

export function EmptyState({ icon, children, action }: { icon?: ReactNode; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="vx-empty" role="status">
      {icon && <span className="vx-empty-icon" aria-hidden>{icon}</span>}
      <p className="vx-empty-text">{children}</p>
      {action && <div className="vx-empty-action">{action}</div>}
    </div>
  );
}

export function Skeleton({ lines = 3, delay = 300 }: { lines?: number; delay?: number }) {
  const [shown, setShown] = useState(delay === 0);
  useEffect(() => { if (delay === 0) return; const t = setTimeout(() => setShown(true), delay); return () => clearTimeout(t); }, [delay]);
  if (!shown) return null;
  return <div className="vx-skeleton" aria-busy="true" aria-label="Loading">{Array.from({ length: lines }, (_, i) => <span key={i} className="vx-skeleton-line" />)}</div>;
}

export function ErrorState({ what, why, fix, detail }: { what: string; why?: string; fix?: ReactNode; detail?: string }) {
  return (
    <div className="vx-error" role="alert">
      <div className="vx-error-head"><AlertTriangle size={16} strokeWidth={1.75} aria-hidden /><span>{what}</span></div>
      {why && <p className="vx-error-why">{why}</p>}
      {fix && <div className="vx-error-fix">{fix}</div>}
      {detail && <details className="vx-error-detail"><summary>Details</summary><pre>{detail}</pre></details>}
    </div>
  );
}

export function Tooltip({ text, children }: { text: string; children: ReactElement<Record<string, unknown>> }) {
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const id = useId();
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [open]);
  if (!isValidElement(children)) return children;
  const trigger = cloneElement(children, {
    "aria-describedby": open ? id : undefined,
    onMouseEnter: () => { timer.current = setTimeout(() => setOpen(true), 500); },
    onMouseLeave: () => { if (timer.current) clearTimeout(timer.current); setOpen(false); },
    onFocus: () => setOpen(true),
    onBlur: () => setOpen(false),
  });
  return <span className="vx-tip-anchor">{trigger}{open && <span role="tooltip" id={id} className="vx-tip">{text}</span>}</span>;
}

export function Popover({ open, onClose, children, align = "start", placement = "bottom", label }: {
  open: boolean; onClose: () => void; children: ReactNode; align?: "start" | "end"; placement?: "bottom" | "top"; label: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const before = document.activeElement as HTMLElement | null;
    const away = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) onClose(); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { onClose(); before?.focus(); } };
    document.addEventListener("pointerdown", away, true);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", away, true); document.removeEventListener("keydown", esc); };
  }, [open, onClose]);
  if (!open) return null;
  return <div ref={box} role="dialog" aria-label={label} className="vx-menu vx-popover" data-align={align} data-placement={placement}>{children}</div>;
}
