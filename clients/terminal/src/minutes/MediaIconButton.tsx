"use client";
import type { ReactNode } from "react";
import "./mediaControls.css";

/** An icon-only control: the label is its accessible name AND its tooltip, so the two never drift. */
export function MediaIconButton({ label, tooltip, onClick, disabled, tone, size, children, pressed }: {
  label: string; tooltip?: string; onClick: () => void; disabled?: boolean; tone?: "danger"; size?: "header";
  children: ReactNode; pressed?: boolean;
}) {
  return <button type="button" className="vx-media-btn" aria-label={label} title={tooltip ?? label}
    aria-pressed={pressed} data-tone={tone} data-size={size} disabled={disabled} onClick={onClick}>{children}</button>;
}
