"use client";
/** ListRow, PanelHeader, Card (guidelines §4.8–4.10).
 *  ListRow: 28px (one line) or 44px (with metadata); selected = surface-2, weight 500 AND a 2px
 *  accent bar (never colour alone); trailing actions appear on hover and focus-within but keep
 *  their space, so the row never reflows. PanelHeader: 44px, title with ellipsis, actions, the
 *  collapse control. Card: one surface step up, radius 10; an interactive card is ONE button. */
import { useState, type ReactNode } from "react";
import { MoreHorizontal, Sparkles } from "lucide-react";
import { Button } from "./Button";
import { Menu, type MenuItem } from "./Menu";
import { Popover } from "./Feedback";

export function ListRow({ title, detail, narrowDetail, meta, leading, actions, trailing, selected, onSelect, twoLine, size = "sm", strong, label, overlayActions }: {
  title: ReactNode;
  /** muted context after the title on the same line ("Oct 23 · Product planning"); it truncates
   *  first, and in a pane under 760px it drops to a second line under the title */
  detail?: ReactNode;
  /** the detail is drawn only in a narrow pane, where it carries what the columns (meta, hidden
   *  there) say on a wide one — so a wide row never says the time twice */
  narrowDetail?: boolean;
  meta?: ReactNode; leading?: ReactNode; actions?: ReactNode;
  /** always-visible cells after the row's button (RowCell columns: a chip, a switch, the row's
   *  actions) — interactive, so outside the row's own button */
  trailing?: ReactNode;
  selected?: boolean; onSelect?: () => void; twoLine?: boolean;
  /** sm = 28px (rails, menus); md = 36px (a page's dense list with 28px actions: Inbox, Meetings) */
  size?: "sm" | "md";
  /** the accessible name of the row's button, when the visible title is not enough */
  label?: string;
  /** a page list whose titles are the content: weight 500, `--text-1` */
  strong?: boolean;
  /** hover actions float over the row's end instead of reserving their width. Never on touch: under
   *  `(hover: none)` they are not drawn at all (R11 — a first tap never reveals an action under the
   *  finger); everything they offer must also be in the row's ⋯ */
  overlayActions?: boolean;
}) {
  return (
    <div className="vx-row2" data-selected={selected ? "" : undefined} data-two-line={twoLine ? "" : undefined} data-size={size === "md" ? "md" : undefined}
      data-strong={strong ? "" : undefined} data-overlay-actions={overlayActions ? "" : undefined} data-has-detail={detail ? "" : undefined} data-narrow-detail={detail && narrowDetail ? "" : undefined}>
      <button type="button" className="vx-row2-main" onClick={onSelect} aria-current={selected || undefined} aria-label={label}>
        {leading && <span className="vx-row2-lead" aria-hidden>{leading}</span>}
        <span className="vx-row2-title">{detail ? <><span className="vx-row2-name">{title}</span><span className="vx-row2-detail">{detail}</span></> : title}</span>
        {meta && <span className="vx-row2-meta">{meta}</span>}
      </button>
      {actions && <span className="vx-row2-actions">{actions}</span>}
      {trailing && <span className="vx-row2-trail">{trailing}</span>}
    </div>
  );
}

/** One fixed-width column inside a ListRow's meta or trailing, so a list of rows lines up as a table.
 *  Widths are a closed set (the column grid), never a style. */
export type RowCellWidth = 16 | 24 | 28 | 52 | 64 | 72 | 88 | 104 | 140 | 160 | 240;
export function RowCell({ w, align, children, hideNarrow }: { w: RowCellWidth; align?: "end"; children?: ReactNode;
  /** dropped when the pane is narrow (a phone): secondary columns such as faces */
  hideNarrow?: boolean }) {
  return <span className="vx-row2-cell" data-w={w} data-align={align} data-hide-narrow={hideNarrow ? "" : undefined}>{children}</span>;
}

/** THE ROW'S ACTION COLUMNS (UI 7m, guidelines pass 2): a suggestion slot (24, a sparkle whose
 *  reason shows on hover AND on tap), ONE verb in a fixed-width column (104, a secondary button —
 *  the same place on every row), and ⋯ (28) with everything else. Any destructive act lives in ⋯,
 *  last and separated. Empty slots keep their width, so the verb never moves. */
export function RowActions({ verb, hint, more, moreLabel = "More" }: {
  verb?: { label: string; onClick: () => void; icon?: ReactNode; disabled?: boolean };
  hint?: { text: string; label?: string };
  more?: MenuItem[];
  moreLabel?: string;
}) {
  const [tip, setTip] = useState(false);
  return <>
    <RowCell w={24}>{hint && <span className="vx-row2-hint">
      <button type="button" className="vx-row2-hint-btn" title={hint.text} aria-label={hint.label ?? hint.text} aria-expanded={tip}
        onClick={(e) => { e.stopPropagation(); setTip((v) => !v); }}><Sparkles size={12} strokeWidth={1.75} aria-hidden /></button>
      <Popover open={tip} onClose={() => setTip(false)} align="end" label={hint.label ?? "Why"}><p className="vx-row2-hint-text">{hint.text}</p></Popover>
    </span>}</RowCell>
    <RowCell w={104} align="end">{verb && <Button variant="secondary" icon={verb.icon} disabled={verb.disabled} data-verb={verb.label}
      onClick={(e) => { e.stopPropagation(); verb.onClick(); }}>{verb.label}</Button>}</RowCell>
    <RowCell w={28}>{more && more.length > 0 && <Menu label={moreLabel} align="end" variant="ghost" items={more}
      trigger={<MoreHorizontal size={14} strokeWidth={1.75} aria-hidden />} />}</RowCell>
  </>;
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
