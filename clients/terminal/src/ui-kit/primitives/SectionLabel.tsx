"use client";
/** SectionLabel (guidelines §2.2) — the one way to name a section: `--text-xs`, weight 500,
 *  `--text-3`, SENTENCE CASE, no letter-spacing. It replaces the uppercase "eyebrows", which came in
 *  44 variants of size, tracking and weight. Labels are a last resort (Refactoring UI): use one only
 *  where the content does not already say what it is. */
import type { ReactNode } from "react";

export function SectionLabel({ children, as = "span" }: { children: ReactNode; as?: "span" | "div" | "h2" | "h3" }) {
  const Tag = as;
  return <Tag className="vx-label">{children}</Tag>;
}
