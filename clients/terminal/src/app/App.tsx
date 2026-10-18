"use client";
/** The composition root — builds the DI container, wires surface commands, renders the workbench.
 *  Importing `../surfaces` registers every surface as a load-time side effect (before this body runs);
 *  `../canvas/register` and `../minutes/register` do the same for the views that sit above surfaces/. */
// FIRST: browser storage is scoped to the signed-in account before any module reads it.
import "../platform/accountScopeInstall";
import "./gate.css";
import { useEffect, useState, type ReactNode } from "react";
import {
  createContainer, reg, ServicesProvider,
  CommandServiceId, createCommandService,
  ContextKeyServiceId, createContextKeyService,
  KeybindingServiceId, createKeybindingService,
} from "../platform";
import { LayoutServiceId, createLayoutService } from "../platform/layout";
import { PaletteServiceId, createPaletteService } from "../workbench/palette";
import { registerEngineCommands } from "../workbench/commands";
import { Workbench } from "../workbench/Workbench";
import { MinutesShell } from "../minutes/MinutesShell";
import { minutesOnly } from "../platform/mode";
import { VersionBar } from "./VersionBar";
import { installFetchDedupe } from "../data/fetchDedupe";
// one request in flight per identical /api read (a home load sent 66 calls in 18 s)
installFetchDedupe();
import { registry } from "../contributions";
import { AuthGate } from "./AuthGate";
import { ApiError, presentError } from "../data/apiClient";
import { SHARED_ARRIVAL_KEY } from "../minutes/roomView";
import { OnboardingGate } from "./OnboardingGate";
import { acceptInvite, acceptTranscriptShare, previewInvite, type InvitePreview } from "../data/workspaceApi";
import { redeemScaffoldShare } from "../minutes/scaffold";
import { beginArrival } from "../minutes/arrival";
import "../surfaces";
import "../canvas/register";   // the canvas registers its views by kind; it sits above surfaces/
import "../minutes/register";  // the minutes shell's page acts and GitHub card, by kind; above surfaces/ too
import { provideDocIntentPoster } from "../ui-kit/docIntents";
import { provideCardInline, provideCardOpener } from "../ui-kit/shellProvided";
import { CardInline } from "../minutes/cards/CardInline";

// A rendered document's acts (an image's "Find it") go through the minutes shell's `postIntent`.
// The ui-kit sits below that shell, so the composition root supplies the door; the shell still loads
// only when the act is pressed, as it did when the ui-kit reached it by `import()` itself.
provideDocIntentPoster((intent) => import("../minutes/extend").then((m) => m.postIntent(intent)));
// the shell parts a rendered document needs (S5 inline card, S7 card reference) — supplied, not imported by the ui-kit
provideCardInline(CardInline);
provideCardOpener((card) => import("../minutes/desktop/cardsApi").then((m) => m.openCard(card)));

const roleLabel = (role: string) => (role === "viewer" ? "read-only" : "read & write");

/** Post-auth invite handling (Lane M/A + M0), rendered INSIDE AuthGate's authed subtree so the preview /
 *  redeem calls carry the user's real API key — the fail-closed gateway rejects anonymous calls, so a
 *  pre-login preview can't authenticate; the consent screen therefore lands right after login. A link may
 *  carry ?invite=<token> (shared WORKSPACE membership) and/or ?tshare=<token> (independent TRANSCRIPT feed):
 *   • ?tshare  → redeemed silently, then the URL is cleaned.
 *   • ?invite  → a CONSENT screen first (what the workspace is + the terms); "Continue to join" redeems and
 *                reloads to a clean URL so the first-view resolver pins the workspace README.
 *  With no invite, children render immediately. */
function InviteGate({ children }: { children: ReactNode }) {
  const [redeeming, setRedeeming] = useState(false);
  const params = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : new URLSearchParams();
  const invite = params.get("invite");
  const tshare = params.get("tshare");
  const meeting = params.get("meeting");   // ?meeting=<platform>/<native> deep-link → open that meeting
  const assign = params.get("assign");     // ?assign=<uid> — MINUTES: choose a group for an unbound meeting
  const view = params.get("view");        // ?view=meeting:<ref>,file:<path>,readme — a COMPOSED layout
  const ask = params.get("ask");           // ?ask=<preset> — MINUTES: open a chat already primed
  const scaffold = params.get("s");        // ?s=<id> — THE SCAFFOLD: one record per arrival (§5.5)

  // MINUTES `?assign=` (from the organiser's artifact email): stash the meeting uid; the groups
  // surface renders the chooser. Same clean-URL discipline as `?meeting=`.
  useEffect(() => {
    if (!assign) return;
    try {
      localStorage.setItem("vexa.assignMeeting", assign);
      const t = params.get("mtitle"); if (t) localStorage.setItem("vexa.assignMeetingTitle", t);
    } catch { /* locked-down storage */ }
    if (!invite && !tshare) window.location.replace(window.location.pathname);
  }, [assign, invite, tshare]);

  // A `?view=` composed-layout deep-link: the sender (usually the person's own agent over MCP)
  // constructs WHICH panes open — e.g. a meeting beside its minutes doc. Same stash-and-clean
  // discipline as `?meeting=`.
  useEffect(() => {
    if (!view) return;
    try { localStorage.setItem("vexa.composedView", view); } catch { /* locked-down storage */ }
    if (!invite && !tshare) window.location.replace(window.location.pathname);
  }, [view, invite, tshare]);

  // A `?ask=<preset>` deep-link — the link an extract email actually carries. It names a preset;
  // the preset BODY lives in `_global/asks/<name>.md`, which only an admin can write. The URL never
  // carries prompt text: a link that could would let anyone who can send mail drive the recipient's
  // agent. `ws` and `meeting` ride along as refs the preset may substitute.
  useEffect(() => {
    if (!ask) return;
    try {
      localStorage.setItem("vexa.pendingPreset", JSON.stringify({
        // `?ws=` IS NOT CARRIED (F97). It used to reach the mount set and the opening's `{{ws}}`
        // token, so a URL could name the workspaces a chat mounted. The mount set is the server's
        // to derive from the caller and the meeting; a link states neither.
        ask, meeting: params.get("meeting") || "",
      }));
    } catch { /* locked-down storage */ }
    if (!invite && !tshare) window.location.replace(window.location.pathname);
  }, [ask, invite, tshare]);

  // `?s=<id>` — THE SCAFFOLD (PRD §5.5). The link carries an ID and nothing else: no prompt text,
  // no mount set, no tabs. The terminal fetches the record as the signed-in identity and renders a
  // chat from it, and the agent is handed the same record — one record, two renderers, so neither
  // side composes from whatever was there before.
  //
  // THE ARRIVAL FINISHES IN THIS DOCUMENT — and that is what makes `?s=` different from every
  // other deep-link above. They clean their URL by RELOADING (`location.replace`), on purpose, so
  // the stash is in place before the grid mounts. This one must not, and the difference is the
  // whole of the 2026-09-05 defect: MinutesShell's effect runs BEFORE this one (React does children
  // first), so by the time the reload fired it had already taken the id out of storage and had a
  // `GET /api/scaffolds/<id>` in flight. The navigation aborted that request, the second document
  // found nothing pending, and neither the chat nor the refusal card ever rendered — a real
  // first-time invitee landed on an empty "New chat". `beginArrival` stashes the id, takes `?s=`
  // off the URL with `history.replaceState` (no request, no unload, nothing aborted) and says
  // *look now*, so either effect order lands. The rules are stated in `minutes/arrival.ts`.
  //
  //  THE SHARE COMES OFF THE RECORD, NOT OFF THE URL (R-A08). When the meeting is not the reader's
  //  own, the scaffold carries a restricted transcript grant. It used to ride this same link as
  //  `&tshare=`, where a bearer credential enters every access log and proxy trace between us and
  //  the inbox. It is redeemed here instead — one authenticated POST against the id the link
  //  already carries — and it runs ALONGSIDE the arrival rather than in front of it:
  //  `redeemScaffoldShare` never throws and answers null for the ordinary no-share case, so the
  //  arrival is never held up by it. It used to gate the URL clean, because `location.replace`
  //  would have aborted it; there is no navigation left to sequence against.
  useEffect(() => {
    if (!scaffold) return;
    beginArrival(scaffold);
    void (async () => {
      const token = await redeemScaffoldShare(scaffold);
      if (!token) return;
      try {
        const r = await acceptTranscriptShare(token);
        if (r?.meeting_id != null) stashSharedMeeting(r.meeting_id);
      } catch (e) { console.error("scaffold transcript share redeem failed:", e); }
    })();
  }, [scaffold]);

  // A `?meeting=` deep-link: stash the ref for the workbench first-view resolver, then clean the URL
  // (reload so the stash is in place before the grid mounts) — unless an invite/tshare owns the reload.
  useEffect(() => {
    if (!meeting) return;
    try {
      localStorage.setItem("vexa.openMeetingRef", meeting);
      const t = params.get("mtitle"); if (t) localStorage.setItem("vexa.openMeetingTitle", t);
    } catch { /* locked-down storage */ }
    if (!invite && !tshare) window.location.replace(window.location.pathname);
  }, [meeting, invite, tshare]);

  // Transcript share redeems silently (no consent surface). Clean the URL after — unless an invite is also
  // present, in which case the invite flow owns the reload.
  //
  // A REFUSED share is said, not swallowed (P18). It used to log to the console and reload into the
  // ordinary workbench, so a person whose invite was for another address, or had been withdrawn,
  // landed on an empty Vexa with no idea why. Now the refusal stays on screen, in words, until they
  // continue.
  const [shareRefused, setShareRefused] = useState<string | null>(null);
  useEffect(() => {
    if (!tshare) return;
    acceptTranscriptShare(tshare)
      .then((r) => {
        if (r?.meeting_id != null) stashSharedMeeting(r.meeting_id);
        if (!invite) window.location.replace(window.location.pathname);
      })
      .catch((e) => setShareRefused(shareRefusalSentence(e)));
  }, [tshare, invite]);

  if (shareRefused) return <ShareRefused message={shareRefused} onContinue={() => window.location.replace(window.location.pathname)} />;
  if (!invite) return <>{children}</>;

  const proceed = async () => {
    setRedeeming(true);
    try {
      const r = await acceptInvite(invite);
      // stash the accepted workspace id so the first-view resolver pins its README post-reload — ALWAYS
      // (even for a returning user with a saved dock): accepting an invite is an explicit "show me this".
      if (r?.workspace_id) localStorage.setItem("vexa.openWorkspace", String(r.workspace_id));
    } catch (e) {
      console.error("workspace invite redeem failed:", e);
    }
    window.location.replace(window.location.pathname);  // strip ?invite → land on the pinned README
  };
  const decline = () => window.location.replace(window.location.pathname);

  return <InviteConsent token={invite} onProceed={proceed} onDecline={decline} busy={redeeming} />;
}

/** After a share redeems, open THAT meeting. Each shell reads its own key: the workbench consumes
 *  `vexa.openMeeting`; the minutes shell opens a meeting from `vexa.openMeetingRef` (a row id is a
 *  valid ref there), which is also what holds its first-room boot back. Found live: a shared link
 *  opened in the minutes product redeemed correctly and then landed on the reader's last chat. */
export function stashSharedMeeting(meetingId: number | string): void {
  try {
    localStorage.setItem("vexa.openMeeting", String(meetingId));
    if (minutesOnly()) {
      localStorage.setItem("vexa.openMeetingRef", String(meetingId));
      // …and on the MEETING's page, not the reader's desk (minutes/roomView.sharedArrivalFront).
      localStorage.setItem(SHARED_ARRIVAL_KEY, "meeting");
    }
  } catch { /* locked-down storage: the meeting is still in their list */ }
}

/** Why a meeting share link did not open, in the reader's words. The server's codes (`not_allowed`,
 *  `revoked`, `expired`, an unknown token) each get a sentence; anything else falls to the presenter. */
export function shareRefusalSentence(e: unknown): string {
  const detail = e instanceof ApiError ? (e.detail || "").trim() : "";
  if (detail === "not_allowed") return "This meeting was shared with a different email address. Sign in with the address the invite was sent to.";
  if (detail === "revoked") return "The owner has withdrawn this share, or removed your access.";
  if (detail === "expired") return "This share link has expired. Ask the owner for a new one.";
  if (e instanceof ApiError && e.status === 404) return "This share link is not valid. Ask the owner for a new one.";
  return presentError(e).headline;
}

function ShareRefused({ message, onContinue }: { message: string; onContinue: () => void }) {
  return (
    <div className="vx-gate-wrap">
      <div role="alert" className="vx-gate-card">
        <div className="vx-gate-refused-title">This meeting could not be opened</div>
        <div className="vx-gate-refused-msg">{message}</div>
        <button onClick={onContinue} className="vx-gate-btn vx-gate-btn-ghost">Continue to Vexa</button>
      </div>
    </div>
  );
}

/** Pre-join CONSENT screen (shown right AFTER login, when a link carries ?invite=). Fetches a read-only
 *  preview of the invite — what the workspace is (its purpose) and the terms (role · who shared it) — so
 *  the invitee knows what they're joining before committing. "Continue to join" redeems and lands on the
 *  workspace README (pinned); "Not now" drops the invite and opens Vexa. */
function InviteConsent({ token, onProceed, onDecline, busy = false }: { token: string; onProceed: () => void; onDecline: () => void; busy?: boolean }) {
  const [pv, setPv] = useState<InvitePreview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    previewInvite(token)
      .then((p) => { if (live) setPv(p); })
      .catch(() => { if (live) setErr("This invite link is invalid or has expired."); });
    return () => { live = false; };
  }, [token]);

  const invalid = Boolean(err) || Boolean(pv && !pv.valid);
  // the look lives in ./gate.css (tokens only)
  const termRow = "vx-gate-term";
  const termK = "c-3";
  const termV = "vx-gate-term-v";
  const btnPrimary = "vx-gate-btn vx-gate-btn-primary";
  const btnGhost = "vx-gate-btn vx-gate-btn-ghost";

  return (
    <div className="vx-gate-wrap">
      <div className="vx-gate-card">
        <div className="t-xs c-3 mb-3 vx-gate-eyebrow">You've been invited to a workspace</div>
        {!pv && !err && <div className="c-3 t-sm">Loading invite…</div>}
        {invalid && (
          <>
            <div className="t-lg c-1 mb-1_5">Invite unavailable</div>
            <div className="t-sm c-3 mb-5">{err || "This invite is no longer valid — it may have been revoked, expired, or already used."}</div>
            <button onClick={onDecline} className={btnGhost}>Continue to Vexa</button>
          </>
        )}
        {pv && pv.valid && (
          <>
            <div className="t-xl fw-600 c-1 mb-1" style={{ wordBreak: "break-word" }}>{pv.name}</div>
            <div className="t-sm c-2 lh-snug mb-4">
              {pv.purpose || <span className="c-3">A shared knowledge workspace.</span>}
            </div>
            <div className={termRow}><span className={termK}>Your access</span><span className={termV}>{roleLabel(pv.role)}</span></div>
            {pv.shared_by && <div className={termRow}><span className={termK}>Shared by</span><span className={termV}>{pv.shared_by}</span></div>}
            <div className="t-xs c-3 lh-snug mt-4 mr-0 mb-5 ml-0 pt-3 bd-t">
              Joining adds this workspace to your set and mounts it into the agent. You can switch it off or leave any time.
            </div>
            <div style={{ display: "flex", gap: 10 }}>
              <button onClick={onProceed} disabled={busy} className={btnPrimary} style={{ opacity: busy ? 0.6 : 1, cursor: busy ? "default" : "pointer" }}>{busy ? "Joining…" : "Continue to join"}</button>
              <button onClick={onDecline} disabled={busy} className={btnGhost}>Not now</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

const container = createContainer([
  reg(ContextKeyServiceId, () => createContextKeyService()),
  reg(CommandServiceId, (c) => createCommandService(c)),
  // Land on Meetings — the Sessions list is retired; the chat lives in the right rail.
  reg(LayoutServiceId, () => createLayoutService("meetings")),
  reg(PaletteServiceId, () => createPaletteService()),
  reg(KeybindingServiceId, (c) => createKeybindingService(c)),
]);
registry.commands().forEach((c) => container.get(CommandServiceId).register(c));
registerEngineCommands(container); // engine commands (palette/layout/open-surface) + default keybindings

export function App() {
  // The workbench is a client-only shell (localStorage-driven layout, dockview). Gate render until
  // mounted so the server HTML (which can't see localStorage/dockview) matches — no hydration mismatch.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return <div className="bg-0" style={{ height: "100vh" }} />;

  // AuthGate logs the user in first (a ?invite= link preserves its query through the OAuth round-trip);
  // InviteGate then shows the consent screen for any ?invite= INSIDE the authed subtree, so its preview /
  // redeem calls carry the user's real API key (the fail-closed gateway rejects anonymous calls).
  return (
    <>
      {/* The swap is invisible now (PRD decision 39) — so the tab tells the person when the
          deployment underneath it moved. OUTSIDE AuthGate on purpose: the tab most likely to be
          running a stale bundle is one sitting on a sign-in screen, and /api/version needs no
          session. Renders nothing until it has something to say. */}
      <VersionBar />
    <AuthGate>
      <InviteGate>
        {/* No setup step in front of the workbench (founder ruling 2026-10-08): `_global` may stay
            empty, so every signed-in person, the admin included, goes straight to onboarding. */}
        <OnboardingGate>
          <ServicesProvider container={container}>
            {minutesOnly() ? <MinutesShell /> : <Workbench />}
          </ServicesProvider>
        </OnboardingGate>
      </InviteGate>
    </AuthGate>
    </>
  );
}
