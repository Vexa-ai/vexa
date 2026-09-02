"use client";
/** The composition root — builds the DI container, wires surface commands, renders the workbench.
 *  Importing `../surfaces` registers every surface as a load-time side effect (before this body runs). */
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import {
  createContainer, reg, ServicesProvider,
  CommandServiceId, createCommandService,
  ContextKeyServiceId, createContextKeyService,
  KeybindingServiceId, createKeybindingService,
} from "../platform";
import { LayoutServiceId, createLayoutService } from "../workbench/layout";
import { PaletteServiceId, createPaletteService } from "../workbench/palette";
import { registerEngineCommands } from "../workbench/commands";
import { Workbench } from "../workbench/Workbench";
import { MinutesShell } from "../minutes/MinutesShell";
import { minutesOnly } from "./mode";
import { registry } from "../contributions";
import { AuthGate } from "./AuthGate";
import { OnboardingGate } from "./OnboardingGate";
import { SetupGate } from "./SetupGate";
import { acceptInvite, acceptTranscriptShare, previewInvite, type InvitePreview } from "../surfaces/workspaceApi";
import "../surfaces";

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
  const setup = params.get("setup");       // ?setup=global — MINUTES: the admin org-tier conversation
  const view = params.get("view");         // ?view=meeting:<ref>,file:<path>,readme — a COMPOSED layout
  const ask = params.get("ask");           // ?ask=<preset> — MINUTES: open a chat already primed
  const scaffold = params.get("s");        // ?s=<id> — THE SCAFFOLD: one record per arrival (§5.5)

  // MINUTES `?setup=global`: the ADMIN company-layer conversation. It stashes a PRESET — the same
  // `vexa.pendingPreset` every emailed `?ask=` link stashes — so the opening turn comes from
  // `_global/asks/setup-global.md`, admin-authored and read hot at click time. It used to stash a
  // bespoke flag that the workbench turned into a prompt string baked into this client, pointing
  // the agent at a file that exists on no deployment; the one conversation that decides how every
  // agent in the company behaves opened by reading nothing, and its wording could only change by
  // rebuilding a cold image. Re-runnable on purpose — amending the company layer is the same
  // conversation.
  useEffect(() => {
    if (setup !== "global") return;
    try {
      localStorage.setItem("vexa.pendingPreset", JSON.stringify({ ask: "setup-global", ws: "_global" }));
    } catch { /* locked-down storage */ }
    if (!invite && !tshare) window.location.replace(window.location.pathname);
  }, [setup, invite, tshare]);

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
        ask, ws: params.get("ws") || "", meeting: params.get("meeting") || "",
      }));
    } catch { /* locked-down storage */ }
    if (!invite && !tshare) window.location.replace(window.location.pathname);
  }, [ask, invite, tshare]);

  // `?s=<id>` — THE SCAFFOLD (PRD §5.5). The link carries an ID and nothing else: no prompt text,
  // no mount set, no tabs. The terminal fetches the record as the signed-in identity and renders a
  // chat from it, and the agent is handed the same record — one record, two renderers, so neither
  // side composes from whatever was there before.
  //
  // Same stash-and-clean discipline as `?ask=`: the URL is cleaned on landing so a reload does not
  // re-open a spent arrival, and the id travels to MinutesShell through storage.
  useEffect(() => {
    if (!scaffold) return;
    try { localStorage.setItem("vexa.pendingScaffold", scaffold); } catch { /* locked-down storage */ }
    if (!invite && !tshare) window.location.replace(window.location.pathname);
  }, [scaffold, invite, tshare]);

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
  useEffect(() => {
    if (!tshare) return;
    acceptTranscriptShare(tshare)
      .then((r) => { if (r?.meeting_id != null) localStorage.setItem("vexa.openMeeting", String(r.meeting_id)); })
      .catch((e) => console.error("transcript share redeem failed:", e))
      .finally(() => { if (!invite) window.location.replace(window.location.pathname); });
  }, [tshare, invite]);

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
  const wrap: CSSProperties = { height: "100vh", background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center", padding: 20 };
  const card: CSSProperties = { width: "100%", maxWidth: 440, background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 14, padding: "26px 26px 24px" };
  const termRow: CSSProperties = { display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 0", borderTop: "1px solid var(--line)", fontSize: 13 };
  const termK: CSSProperties = { color: "var(--t3)" };
  const termV: CSSProperties = { color: "var(--t1)", fontWeight: 500, maxWidth: "60%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
  const btnBase: CSSProperties = { padding: "9px 16px", borderRadius: 8, fontSize: 13, cursor: "pointer", border: "1px solid var(--line)" };
  const btnPrimary: CSSProperties = { ...btnBase, background: "var(--accent)", color: "var(--on-accent)", border: "1px solid var(--accent)", fontWeight: 600 };
  const btnGhost: CSSProperties = { ...btnBase, background: "transparent", color: "var(--t2)" };

  return (
    <div style={wrap}>
      <div style={card}>
        <div style={{ fontSize: 11.5, textTransform: "uppercase", letterSpacing: ".08em", color: "var(--t3)", marginBottom: 12 }}>You've been invited to a workspace</div>
        {!pv && !err && <div style={{ color: "var(--t3)", fontSize: 13 }}>Loading invite…</div>}
        {invalid && (
          <>
            <div style={{ fontSize: 16, color: "var(--t1)", marginBottom: 6 }}>Invite unavailable</div>
            <div style={{ fontSize: 13, color: "var(--t3)", marginBottom: 20 }}>{err || "This invite is no longer valid — it may have been revoked, expired, or already used."}</div>
            <button onClick={onDecline} style={btnGhost}>Continue to Vexa</button>
          </>
        )}
        {pv && pv.valid && (
          <>
            <div style={{ fontSize: 21, fontWeight: 600, color: "var(--t1)", marginBottom: 5, wordBreak: "break-word" }}>{pv.name}</div>
            <div style={{ fontSize: 13.5, color: "var(--t2)", lineHeight: 1.5, marginBottom: 18 }}>
              {pv.purpose || <span style={{ color: "var(--t3)" }}>A shared knowledge workspace.</span>}
            </div>
            <div style={termRow}><span style={termK}>Your access</span><span style={termV}>{roleLabel(pv.role)}</span></div>
            {pv.shared_by && <div style={termRow}><span style={termK}>Shared by</span><span style={termV}>{pv.shared_by}</span></div>}
            <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.5, margin: "16px 0 20px", paddingTop: 14, borderTop: "1px solid var(--line)" }}>
              Joining adds this workspace to your set and mounts it into the agent. You can switch it off or leave any time.
            </div>
            <div style={{ display: "flex", gap: 10 }}>
              <button onClick={onProceed} disabled={busy} style={{ ...btnPrimary, opacity: busy ? 0.6 : 1, cursor: busy ? "default" : "pointer" }}>{busy ? "Joining…" : "Continue to join"}</button>
              <button onClick={onDecline} disabled={busy} style={btnGhost}>Not now</button>
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
  if (!mounted) return <div style={{ height: "100vh", background: "var(--bg)" }} />;

  // AuthGate logs the user in first (a ?invite= link preserves its query through the OAuth round-trip);
  // InviteGate then shows the consent screen for any ?invite= INSIDE the authed subtree, so its preview /
  // redeem calls carry the user's real API key (the fail-closed gateway rejects anonymous calls).
  return (
    <AuthGate>
      <InviteGate>
        {/* SetupGate: the bootstrap-claimed admin's first-run wizard (models + transcription,
            smoke-tested). Non-admins and completed instances fall straight through. */}
        <SetupGate>
          <OnboardingGate>
            <ServicesProvider container={container}>
              {minutesOnly() ? <MinutesShell /> : <Workbench />}
            </ServicesProvider>
          </OnboardingGate>
        </SetupGate>
      </InviteGate>
    </AuthGate>
  );
}
