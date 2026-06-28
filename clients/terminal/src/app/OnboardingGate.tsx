"use client";
/** OnboardingGate — sits between auth and the workbench. On a brand-new user (durable per-user flag) it
 *  materializes the workspace (`initWorkspace`, idempotent) and SEEDS a cached onboarding greeting into the
 *  chat — instantly, with no slow LLM round-trip — then arms the chat so the user's first reply carries the
 *  discovery-loop grounding. An already-onboarded user falls straight through to the workbench. */
import { useEffect } from "react";
import { initWorkspace } from "../surfaces/workspaceApi";
import { ONBOARDING_SEED_EVENT } from "../canvas/actions";
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
      // Let the chat surface mount + register its listener, then seed the cached greeting into it.
      window.setTimeout(() => window.dispatchEvent(new CustomEvent(ONBOARDING_SEED_EVENT)), 600);
    })();
  }, []);

  return <>{children}</>;
}
