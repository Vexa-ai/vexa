"use client";
/** Drawer — the rail's overlay form (guidelines §3.1): a Sheet from the LEFT, opened from the
 *  rail's icon strip (compact, narrow) or the header's menu control (single). Everything a Sheet
 *  promises holds: modal while open, focus trapped and returned, Esc and the scrim close it. */
import type { ReactNode } from "react";
import { Sheet } from "./Sheet";

export function Drawer({ open, onClose, width, label, children }: {
  open: boolean; onClose: () => void; width: number; label: string; children: ReactNode;
}) {
  return <Sheet form="overlay" side="left" open={open} onClose={onClose} width={width} label={label} dataPane="rail-drawer">{children}</Sheet>;
}
