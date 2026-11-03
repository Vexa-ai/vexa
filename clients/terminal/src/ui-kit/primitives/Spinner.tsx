"use client";
/** Spinner, StatusDot, Kbd (guidelines §4.18): small, decorative units. A spinner is `aria-hidden`
 *  and always sits beside a text label; a status dot is paired with a word, never colour alone. */
export function Spinner({ size = 16 }: { size?: 14 | 16 }) {
  return <span className="vx-spinner" data-size={size} aria-hidden />;
}
export type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "meeting" | "accent";
export function StatusDot({ tone = "neutral", label }: { tone?: Tone; label: string }) {
  return <span className="vx-status"><span className="vx-dot" data-tone={tone} aria-hidden /><span>{label}</span></span>;
}
export function Kbd({ children }: { children: string }) {
  return <kbd className="vx-kbd">{children}</kbd>;
}
