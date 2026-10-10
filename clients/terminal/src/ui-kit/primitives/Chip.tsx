"use client";
/** Badge, Tag, Chip, EntityChip (guidelines §4.5) — static is a badge, interactive is a chip, and
 *  they never share a shape. A Badge (radius 4, tint fill, no border) is status or a count and is
 *  never clickable; a Tag (radius 4, surface fill) names a category; a Chip (radius 6, 24px, a
 *  border) is a token the reader acts on — open, target, remove. No uppercase, no accent on badges
 *  or tags, no dashed borders: an "add" chip is a ghost chip with a plus. */
import type { ReactNode } from "react";
import { Building2, CalendarClock, FileText, User, X } from "lucide-react";
import type { Tone } from "./Spinner";

export function Badge({ tone = "neutral", dot, children }: { tone?: Exclude<Tone, "accent">; dot?: boolean; children: ReactNode }) {
  return <span className="vx-badge" data-tone={tone}>{dot && <span className="vx-dot" data-tone={tone} aria-hidden />}{children}</span>;
}
export function Tag({ children }: { children: ReactNode }) {
  return <span className="vx-tag">{children}</span>;
}
export function Chip({ children, icon, selected, ghost, onClick, onRemove, removeLabel, title, data }: {
  children: ReactNode; icon?: ReactNode; selected?: boolean; ghost?: boolean;
  onClick?: () => void; onRemove?: () => void; removeLabel?: string; title?: string; data?: Record<string, string>;
}) {
  return (
    <span className="vx-chip" data-selected={selected ? "" : undefined} data-ghost={ghost ? "" : undefined} {...data}>
      <button type="button" className="vx-chip-main" onClick={onClick} title={title} aria-current={selected || undefined}>
        {icon && <span className="vx-chip-icon" aria-hidden>{icon}</span>}
        <span className="vx-chip-label">{children}</span>
      </button>
      {onRemove && (
        <button type="button" className="vx-chip-x" aria-label={removeLabel ?? "Remove"} title={removeLabel ?? "Remove"} onClick={onRemove}>
          <X size={12} strokeWidth={1.75} aria-hidden />
        </button>
      )}
    </span>
  );
}
const ENTITY_ICON = {
  person: <User size={14} strokeWidth={1.75} />, company: <Building2 size={14} strokeWidth={1.75} />,
  meeting: <CalendarClock size={14} strokeWidth={1.75} />, doc: <FileText size={14} strokeWidth={1.75} />,
};
export function EntityChip({ kind, children, onOpen, title }: {
  kind: "person" | "company" | "meeting" | "doc"; children: ReactNode; onOpen: () => void; title?: string;
}) {
  return (
    <button type="button" className="vx-chip vx-entity" data-kind={kind} onClick={onOpen} title={title}>
      <span className="vx-chip-icon" aria-hidden>{ENTITY_ICON[kind]}</span>
      <span className="vx-chip-label">{children}</span>
    </button>
  );
}
