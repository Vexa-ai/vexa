"use client";
/** Toast (guidelines §4.12) — `sonner` (MIT, already a dependency), themed by the tokens. Only for
 *  a completed background outcome ("Workspace shared") or an Undo on a reversible act; never for
 *  an error that needs input — those stay inline beside their cause. Mount `<Toaster />` once. */
import { Toaster as Sonner, toast as sonnerToast } from "sonner";

export function Toaster() {
  return <Sonner position="bottom-right" duration={5000} className="vx-toaster"
    toastOptions={{ className: "vx-toast" }} />;
}
export const toast = (message: string, opts?: { undo?: () => void }) =>
  sonnerToast(message, opts?.undo ? { action: { label: "Undo", onClick: opts.undo } } : undefined);
