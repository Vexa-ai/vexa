"use client";
/** A calm toast for what went wrong on the rail (#1783; founder: the error landed as raw text over the
 *  account card). One line that says what to do, a dismiss, and it goes by itself after a while. */
import { useEffect } from "react";
import { X } from "lucide-react";

export function RailToast({ message, onDismiss, ms = 8000 }: { message: string; onDismiss: () => void; ms?: number }) {
  useEffect(() => { const t = window.setTimeout(onDismiss, ms); return () => window.clearTimeout(t); }, [message, ms, onDismiss]);
  return (
    <div role="alert" className="vx-toast" data-rail-toast="">
      <span className="vx-toast-text">{message}</span>
      <button type="button" className="vx-toast-x" aria-label="Dismiss" onClick={onDismiss}><X size={14} strokeWidth={1.75} /></button>
    </div>
  );
}
