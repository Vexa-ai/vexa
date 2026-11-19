"use client";
/** ChipRow (guidelines §3.3) — chips on ONE line. When they do not all fit, the row fades at its
 *  end and a "+N" chip says how many are out of view; pressing it lets the row wrap so every chip
 *  shows, and pressing again folds it back. Nothing is ever silently clipped.
 *
 *  Each direct child is one chip. Measuring is by the children's boxes against the row's, so the
 *  row needs no knowledge of what a chip contains. */
import { Children, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

export function ChipRow({ children, label = "chips" }: { children: ReactNode; label?: string }) {
  const row = useRef<HTMLDivElement>(null);
  const [hidden, setHidden] = useState(0);
  const [open, setOpen] = useState(false);
  const count = Children.toArray(children).filter(Boolean).length;

  const measure = useCallback(() => {
    const el = row.current;
    if (!el || open) return;
    const box = el.getBoundingClientRect();
    let n = 0;
    for (const c of Array.from(el.children) as HTMLElement[]) {
      if (c.dataset.chiprowMore !== undefined) continue;
      const r = c.getBoundingClientRect();
      if (r.width > 0 && r.right > box.right + 1) n++;
    }
    setHidden((h) => (h === n ? h : n));
  }, [open]);

  useLayoutEffect(() => { measure(); });
  useEffect(() => {
    const el = row.current;
    if (!el) return;
    window.addEventListener("resize", measure);
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(el);
    return () => { window.removeEventListener("resize", measure); ro?.disconnect(); };
  }, [measure]);
  useEffect(() => { if (count === 0) setOpen(false); }, [count]);

  if (count === 0) return null;
  return (
    <div className="vx-chiprow" data-open={open ? "" : undefined} data-clipped={!open && hidden > 0 ? "" : undefined}>
      <div ref={row} className="vx-chiprow-items" role="group" aria-label={label}>{children}</div>
      {(hidden > 0 || open) && (
        <button type="button" className="vx-chip vx-chiprow-more" data-chiprow-more="" aria-expanded={open}
          aria-label={open ? `Show fewer ${label}` : `${hidden} more ${label}`} onClick={() => setOpen((v) => !v)}>
          <span className="vx-chip-main">{open ? "Fewer" : `+${hidden}`}</span>
        </button>
      )}
    </div>
  );
}
