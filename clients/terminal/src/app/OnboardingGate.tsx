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
 *  ── EXCEPT WHILE THE COMPANY LAYER IS MISSING (founder ruling 2026-09-02) ──────────────────────
 *  Watching a real first admin click, the founder got a Personal chat opened on the ordinary
 *  greeting — "I'm your agent here… paste a meeting link" — on an instance that could not join a
 *  meeting, could not send a mail, and served nobody, because its company layer had not been
 *  written yet. His words: "this is what I get from the first admin click — it should want to setup
 *  global here."
 *
 *  This gate fired that greeting. It is ONE place, and it is the place that decides what exists on
 *  the very first render, which is why the suppression belongs here rather than as a flag threaded
 *  through every surface that reacts to the seed event. While `/api/global/state` says the layer is
 *  missing, this gate does nothing at all: no workspace init, no greeting, and — importantly — NO
 *  durable onboarded flag, so the personal onboarding still happens, on the first load after the
 *  instance opens. It is deferred, not cancelled.
 *
 *  Two decisions inside that are easy to get backwards:
 *
 *  • It gates on `global_setup` alone, not on `you_are_admin`. By the time this component mounts,
 *    AuthGate has already refused everybody except the admin on a gated instance (its four-row
 *    verdict), so the only person who can reach this line while the gate is up IS the admin. Adding
 *    the second condition would be a second copy of a decision AuthGate already owns.
 *
 *  • A probe that THROWS seeds anyway. The fail directions are not symmetric: failing closed on a
 *    network blip silently kills onboarding for every ordinary new user of a healthy instance —
 *    permanently, since they arrive once — while failing open costs, at worst, one stray greeting on
 *    an instance that is about to be set up. Same direction AuthGate's own probes take. */
import { useEffect } from "react";
import { initWorkspace } from "../surfaces/workspaceApi";
import { getGlobalState } from "../surfaces/settingsApi";
import { isOnboarded, setOnboarded } from "./onboardingState";

// Module-scoped so the bootstrap runs EXACTLY ONCE per page load — React StrictMode (dev) double-invokes
// effects, which otherwise fires `init` twice (the 2nd races the seed → 500).
let bootstrapped = false;

/** Should the first-run personal onboarding happen at all right now?
 *
 *  Pure, and exported, because the fail direction above is the whole of the decision and a comment
 *  is not a test. `null` = the probe could not answer.
 *
 *  ⚠ NOT `state.you_are_admin` — see the header. */
export function shouldSeedOnboarding(globalSetup: "completed" | "missing" | null): boolean {
  return globalSetup !== "missing";
}

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
      // The company-layer gate. Probed only for a user who has NOT been onboarded, so the ordinary
      // page load of an ordinary day pays nothing for it.
      const globalSetup = await getGlobalState().then((s) => s.global_setup).catch(() => null);
      if (!shouldSeedOnboarding(globalSetup)) {
        // Deliberately leave `bootstrapped` set and the onboarded flag UNSET: nothing more should
        // happen on this page load, and everything should still happen on the first load after the
        // instance opens.
        return;
      }
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
