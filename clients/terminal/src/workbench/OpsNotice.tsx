"use client";
/** OpsNotice — the "ops in progress" pill in the workbench header. Polls /api/ops-status (a file
 *  the deploy operator sets before touching infra) so users can tell a maintenance blip from a
 *  product bug — a stuck chat under an active notice is expected, not broken. Poll errors keep the
 *  LAST known state: mid-deploy the terminal itself may briefly fail to answer, and a notice that
 *  vanished exactly during the disruption it announces would defeat the point. */
import { useEffect, useState } from "react";
import { Icon } from "../ui-kit";

const POLL_MS = 20000;

export function OpsNotice() {
  const [status, setStatus] = useState<{ active: boolean; message?: string }>({ active: false });
  useEffect(() => {
    let live = true;
    const poll = async () => {
      try {
        const r = await fetch("/api/ops-status", { cache: "no-store" });
        if (!r.ok) return;
        const s = (await r.json()) as { active?: boolean; message?: string };
        if (live) setStatus({ active: s.active === true, message: s.message });
      } catch { /* keep last known state — see header comment */ }
    };
    void poll();
    const iv = setInterval(() => void poll(), POLL_MS);
    return () => { live = false; clearInterval(iv); };
  }, []);

  if (!status.active) return null;
  return (
    <div title="Maintenance in progress — brief interruptions (chat pauses, reconnects) are expected until this notice clears."
      style={{ display: "flex", alignItems: "center", gap: 7, padding: "3px 10px", borderRadius: 999,
        background: "color-mix(in srgb, var(--warn, #d4a72c) 18%, transparent)",
        border: "1px solid color-mix(in srgb, var(--warn, #d4a72c) 45%, transparent)",
        color: "var(--t1)", fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>
      <Icon name="zap" size={12} style={{ color: "var(--warn, #d4a72c)", flex: "none" }} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
        {status.message || "Maintenance in progress — brief interruptions expected"}
      </span>
    </div>
  );
}
