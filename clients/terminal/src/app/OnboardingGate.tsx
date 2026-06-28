"use client";
/** OnboardingGate — sits between auth and the workbench. On first entry it materializes the workspace
 *  (`initWorkspace`, idempotent) and, if it was just SEEDED (a brand-new user) — or `?onboard=1` forces it —
 *  KICKS OFF onboarding IN THE CHAT: one turn that tells the agent to interview the user ONE QUESTION AT A
 *  TIME and scaffold the workspace from the answers. No form — the agent drives, the user answers in chat.
 *  An existing workspace falls straight through to the workbench. */
import { useEffect } from "react";
import { initWorkspace } from "../surfaces/workspaceApi";
import { ASK_CHAT_EVENT, ONBOARDING_KICKOFF_MARK } from "../canvas/actions";
import { isOnboarded, setOnboarded } from "./onboardingState";

const KICKOFF = ONBOARDING_KICKOFF_MARK + [
  "Read these workspace files before answering (use the Read tool): onboarding.md",
  "",
  "Onboard me by following the discovery-loop playbook in onboarding.md. First, in one or two sentences,",
  "tell me what you're building and why. Then ask only for the minimum seed you need to start (my name +",
  "LinkedIn or company). After that, RESEARCH autonomously and deeply with web search before asking me",
  "anything else — never bounce back a fact you can find online, and run at least two discovery cycles,",
  "only asking me about the genuine gaps you can't resolve yourself (and say why each one matters).",
].join("\n");

// Module-scoped so the bootstrap runs EXACTLY ONCE per page load — React StrictMode (dev) double-invokes
// effects, which otherwise fires `init` twice (the 2nd races the seed → 500) and drops the kickoff.
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
      // Let the chat surface mount + register its ASK listener, then fire the kickoff into it.
      window.setTimeout(() => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, {
        detail: { prompt: KICKOFF, hidden: true, ground: false },   // system kickoff: no user bubble, no meeting context
      })), 600);
    })();
  }, []);

  return <>{children}</>;
}
