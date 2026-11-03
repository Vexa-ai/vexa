"use client";
/** VersionBar — one line, one button, and it appears only when the tab has gone stale.
 *
 *  PRD decision 39: *"a version endpoint; a new server or bundle version shows a one-line 'new
 *  version, reload' bar; reload only on click."* All three constraints are load-bearing:
 *
 *  • ONE LINE, because the alternative that keeps being proposed — a modal, a toast, a full-screen
 *    "updating" state — interrupts a person who is mid-sentence in a chat. The swap did not
 *    interrupt them; the notice must not either.
 *  • RELOAD ONLY ON CLICK. An automatic reload throws away an unsent message and an open canvas
 *    to fix a problem the person may not have. The tab keeps working on the old bundle; the bar
 *    just stops the case where they are debugging a fix that is already live and invisible.
 *  • It never un-shows itself. Once the deployment has moved, no later poll makes the tab fresh
 *    again, so the bar is sticky and polling stops the moment it appears.
 */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { POLL_MS, baselineOf, foldBaseline, readVersion, reloadOffered, type Baseline } from "./versionWatch";

const bar: CSSProperties = {
  position: "fixed", top: 0, left: 0, right: 0, zIndex: 9999,
  display: "flex", alignItems: "center", justifyContent: "center", gap: 12,
  padding: "7px 14px", fontSize: 13, lineHeight: 1.3,
  background: "var(--panel)", color: "var(--t1)", borderBottom: "1px solid var(--line)",
};
const button: CSSProperties = {
  padding: "3px 12px", borderRadius: 7, fontSize: 12.5, fontWeight: 600, cursor: "pointer",
  background: "var(--accent)", color: "var(--on-accent)", border: "1px solid var(--accent)",
};

export function VersionBar({ reload = () => window.location.reload() }: { reload?: () => void }) {
  const [stale, setStale] = useState(false);
  const baseline = useRef<Baseline | null>(null);

  const poll = useCallback(async () => {
    const report = await readVersion();
    if (!report) return;                       // a failed poll is silence, not a banner
    if (baseline.current === null) { baseline.current = baselineOf(report); return; }
    if (reloadOffered(baseline.current, report)) setStale(true);
    else baseline.current = foldBaseline(baseline.current, report);
  }, []);

  useEffect(() => {
    if (stale) return;                         // sticky: stop asking once the answer is known
    let live = true;
    const tick = () => { if (live) void poll(); };
    tick();
    const timer = setInterval(tick, POLL_MS);
    // Focus is the trigger that fires in practice: a swap lands far more often while the tab is in
    // the background than while someone is typing into it.
    const onFocus = () => { if (document.visibilityState === "visible") tick(); };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      live = false;
      clearInterval(timer);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [poll, stale]);

  if (!stale) return null;
  return (
    <div style={bar} role="status">
      <span>A new version is ready — reload</span>
      <button style={button} onClick={reload}>Reload</button>
    </div>
  );
}
