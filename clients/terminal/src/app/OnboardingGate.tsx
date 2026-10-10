"use client";
/** OnboardingGate — sits between auth and the workbench. On a brand-new user (durable per-user flag) it
 *  materializes the workspace (`initWorkspace`, idempotent) and marks them onboarded. An
 *  already-onboarded user falls straight through to the workbench.
 *
 *  ── IT NO LONGER GREETS (founder ruling 2026-09-02, F36) ───────────────────────────────────────
 *  It used to also seed a cached onboarding greeting into the chat — a first turn nobody typed,
 *  written instantly so there was no model round-trip to wait for. That is the "I'm your agent
 *  here… paste a meeting link" the founder met in a chat he had never created: *"i do not like this
 *  text."* A new chat now shows an empty composer and nothing else, so the seed, the event that
 *  carried it and the greeting it wrote are all deleted.
 *
 *  What is left is the half that is not a message: MATERIALISING THE WORKSPACE. That is why this
 *  component stays rather than going with the greeting — deleting it would take the idempotent
 *  `initWorkspace` and the durable per-user flag with it, and both are load-bearing.
 *
 *  ── FOR EVERYBODY, FROM THE FIRST SIGN-IN (founder ruling 2026-10-08) ──────────────────────────
 *  "let's remove global setup at all so that there is no need to setup global at all - let it be
 *  empty with no data - it's fine." This used to hold the onboarding back while the company layer
 *  in `_global` was unwritten (the 2026-09-02 gate). There is no such state any more: the admin and
 *  everyone else are seeded the same way, on their first load, whatever `_global` holds. */
import { useEffect } from "react";
import { initWorkspace } from "../surfaces/workspaceApi";
import { isOnboarded, setOnboarded } from "./onboardingState";

// Module-scoped so the bootstrap runs EXACTLY ONCE per page load — React StrictMode (dev) double-invokes
// effects, which otherwise fires `init` twice (the 2nd races the seed → 500).
let bootstrapped = false;

export function OnboardingGate({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    if (bootstrapped) return;
    bootstrapped = true;
    void (async () => {
      const forced = typeof window !== "undefined" && new URLSearchParams(window.location.search).has("onboard");
      // Identify the user, then gate on the DURABLE per-user flag — not the transient init `seeded`
      // (which is reload-dependent). Onboarding fires exactly once per user and survives refreshes.
      const me = await fetch("/api/auth/me", { cache: "no-store" }).then((r) => r.json()).catch(() => null);
      const uid = (me?.user?.email as string) || "anon";
      if (!forced && isOnboarded(uid)) return;   // already onboarded → straight to the workbench
      await initWorkspace().catch(() => null);   // ensure the workspace exists (idempotent)
      setOnboarded(uid, true);                   // flip the durable bool BEFORE firing → a reload never re-runs it
      if (window.location.search) window.history.replaceState({}, "", window.location.pathname);
    })();
  }, []);

  return <>{children}</>;
}

/** Test seam ONLY — the module-scoped once-per-page-load latch above is exactly right in a browser
 *  and exactly wrong in a test file that renders the component more than once. */
export function __resetOnboardingBootstrap(): void {
  bootstrapped = false;
}
