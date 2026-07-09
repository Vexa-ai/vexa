"use client";
/** Modal — a minimal centered overlay dialog: dimmed backdrop, Esc / backdrop-click to close, a titled
 *  card. Portaled to <body> so it escapes the sidebar's overflow/stacking context. Used for creation
 *  dialogs (attach repo, …) that shouldn't live inline in the workspaces list. */
import { useEffect, type ReactNode, type CSSProperties } from "react";
import { createPortal } from "react-dom";

export function Modal({ title, onClose, children, width = 400 }: {
  title: string; onClose: () => void; children: ReactNode; width?: number;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const backdrop: CSSProperties = { position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, padding: 20 };
  const card: CSSProperties = { width: "100%", maxWidth: width, background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 12, boxShadow: "0 12px 40px rgba(0,0,0,.45)", padding: 18 };
  const head: CSSProperties = { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 };

  const body = (
    // mousedown on the backdrop itself (not a child) closes — avoids closing on a drag that ends outside.
    <div style={backdrop} onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={card} role="dialog" aria-modal="true" aria-label={title}>
        <div style={head}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--t1)" }}>{title}</div>
          <span onClick={onClose} title="Close (Esc)" style={{ cursor: "pointer", color: "var(--t3)", fontSize: 17, lineHeight: 1, padding: "0 3px" }}>×</span>
        </div>
        {children}
      </div>
    </div>
  );
  return typeof document !== "undefined" ? createPortal(body, document.body) : null;
}
