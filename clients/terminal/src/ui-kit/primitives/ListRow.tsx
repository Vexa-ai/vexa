"use client";
/** ListRow, PanelHeader, Card (guidelines §4.8–4.10).
 *  ListRow: 28px (one line) or 44px (with metadata); selected = surface-2, weight 500 AND a 2px
 *  accent bar (never colour alone); trailing actions appear on hover and focus-within but keep
 *  their space, so the row never reflows. PanelHeader: 44px, title with ellipsis, actions, the
 *  collapse control. Card: one surface step up, radius 10; an interactive card is ONE button. */
import type { ReactNode } from "react";

export function ListRow({ title, meta, leading, actions, selected, onSelect, twoLine }: {
  title: ReactNode; meta?: ReactNode; leading?: ReactNode; actions?: ReactNode;
  selected?: boolean; onSelect?: () => void; twoLine?: boolean;
}) {
  return (
    <div className="vx-row2" data-selected={selected ? "" : undefined} data-two-line={twoLine ? "" : undefined}>
      <button type="button" className="vx-row2-main" onClick={onSelect} aria-current={selected || undefined}>
        {leading && <span className="vx-row2-lead" aria-hidden>{leading}</span>}
        <span className="vx-row2-title">{title}</span>
        {meta && <span className="vx-row2-meta">{meta}</span>}
      </button>
      {actions && <span className="vx-row2-actions">{actions}</span>}
    </div>
  );
}

export function PanelHeader({ title, actions, trailing, inset = "panel" }: {
  title: ReactNode; actions?: ReactNode; trailing?: ReactNode; inset?: "rail" | "panel";
}) {
  return (
    <div className="vx-panelhead" data-inset={inset}>
      <span className="vx-panelhead-title">{title}</span>
      <span className="vx-panelhead-gap" />
      {actions}
      {trailing}
    </div>
  );
}

export function Card({ title, actions, footer, children, onOpen, raised }: {
  title?: ReactNode; actions?: ReactNode; footer?: ReactNode; children: ReactNode; onOpen?: () => void; raised?: boolean;
}) {
  const body = <>
    {(title || actions) && <div className="vx-card-head"><span className="vx-card-title">{title}</span>{actions}</div>}
    <div className="vx-card-body">{children}</div>
    {footer && <div className="vx-card-foot">{footer}</div>}
  </>;
  return onOpen
    ? <button type="button" className="vx-card" data-raised={raised ? "" : undefined} data-interactive="" onClick={onOpen}>{body}</button>
    : <section className="vx-card" data-raised={raised ? "" : undefined}>{body}</section>;
}
