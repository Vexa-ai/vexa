"use client";
import { useEffect, useRef } from "react";

export type ContextMenuItem = {
  id: string;
  label: string;
  detail?: string;
  onSelect: () => void | Promise<void>;
};

export async function copyText(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      /* fall through to the textarea copy path */
    }
  }
  if (typeof document === "undefined") return;
  const node = document.createElement("textarea");
  node.value = text;
  node.setAttribute("readonly", "true");
  node.style.position = "fixed";
  node.style.left = "-9999px";
  document.body.appendChild(node);
  node.select();
  try { document.execCommand("copy"); } finally { document.body.removeChild(node); }
}

export function ContextMenu({ x, y, items, onClose }: {
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const closeOutside = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    const closeEscape = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };  // consume: close-topmost beats nav.back
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeEscape);
    };
  }, [onClose]);

  const width = 188;
  const estimatedHeight = items.length * 32 + 8;
  const left = typeof window === "undefined" ? x : Math.max(8, Math.min(x, window.innerWidth - width - 8));
  const top = typeof window === "undefined" ? y : Math.max(8, Math.min(y, window.innerHeight - estimatedHeight - 8));

  return (
    <div ref={ref} role="menu" onContextMenu={(e) => e.preventDefault()}
      style={{ position: "fixed", left, top, width, zIndex: 1000, padding: 4, background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 8, boxShadow: "0 10px 30px rgba(0,0,0,.38)" }}>
      {items.map((item) => (
        <button key={item.id} role="menuitem" onClick={(e) => { e.stopPropagation(); onClose(); void item.onSelect(); }}
          style={{ width: "100%", display: "flex", flexDirection: "column", alignItems: "stretch", gap: 1, border: "none", borderRadius: 6, background: "transparent", color: "var(--t1)", cursor: "pointer", padding: "6px 9px", textAlign: "left", fontSize: 12.5 }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
          <span>{item.label}</span>
          {item.detail && <span style={{ color: "var(--t3)", fontFamily: "var(--mono)", fontSize: 10.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{item.detail}</span>}
        </button>
      ))}
    </div>
  );
}
