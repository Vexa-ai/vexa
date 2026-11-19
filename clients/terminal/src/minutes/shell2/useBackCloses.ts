/** SYSTEM BACK CLOSES THE TOP LAYER (mobile G2, fix 8; feature `shell2`). While an overlay is open
 *  (the chat drawer, the pages sheet), one history entry stands for it: Android back or the iOS edge
 *  swipe pops that entry and the overlay closes, instead of the browser leaving the app. Closing it
 *  any other way takes its entry back off, so history never fills with dead steps. */
import { useEffect, useRef } from "react";

export function useBackCloses(open: boolean, close: () => void, key: string): void {
  const closeRef = useRef(close);
  closeRef.current = close;
  const pushed = useRef(false);
  useEffect(() => {
    if (!open) {
      if (pushed.current) {
        pushed.current = false;
        try { if ((window.history.state as Record<string, unknown> | null)?.vexaLayer === key) window.history.back(); } catch { /* no history */ }
      }
      return;
    }
    try { window.history.pushState({ ...((window.history.state as Record<string, unknown> | null) ?? {}), vexaLayer: key }, ""); pushed.current = true; } catch { return; }
    const onPop = (e: PopStateEvent) => {
      if ((e.state as Record<string, unknown> | null)?.vexaLayer === key) return;
      if (!pushed.current) return;
      pushed.current = false;
      closeRef.current();
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [open, key]);
}
