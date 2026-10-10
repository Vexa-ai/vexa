"use client";
/** Meeting-prep tab (center) — a PLANNED meeting's home while it hasn't happened yet.
 *
 *  Opens when a row in an intent status (`idle`/`scheduled`) is clicked. Everything here edits the
 *  SAME meetings row the bot will later claim: title / time / link (PATCH by row id), the auto-join
 *  toggle, and the WORKSPACE BIND — the sharing mechanism (members of the bound workspace see this
 *  meeting, its live feed, and later its transcript). "Share" mints a workspace invite link; the
 *  prep JTBD is: bind (or create) a prep workspace → research into it with the agent → share it
 *  with the people you're meeting → the bot auto-joins at start → notes land on the same row.
 *  Once the row leaves the intent statuses the row click routes to the live meeting tab instead. */
import { useEffect, useMemo, useRef, useState } from "react";
import { registerTab, type TabProps } from "../contributions";
import { useService, ASK_CHAT_EVENT } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { ConfirmDialog, Icon } from "../ui-kit";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { DateTimePicker } from "../ui-kit/DateTimePicker";
import { copyText } from "../ui-kit/ContextMenu";
import { useLiveMeetings, refreshMeetings } from "./liveMeetings";
import type { MeetingMock } from "./meetingModel";
import { presentError, readApiFailure } from "./apiClient";
import { resolveJoinError, type ServiceDenialPresentation } from "./serviceDenial";
import { ServiceDenialPanel } from "./ServiceDenialPanel";
import { createPlannedMeeting, updatePlannedMeeting, deletePlannedMeeting } from "./plannedApi";
import { createSharedWorkspace, listSharedMemberships, listWorkspaceTree, mintInvite, readWorkspaceFile, type Membership } from "./workspaceApi";
import { findBriefNote, isExampleNote } from "./briefNote";
import { manageTabDescriptor } from "./workspaceManage";
import { defaultBotName } from "./defaultBotName";

const field = {
  fontSize: 12.5, padding: "6px 8px", background: "var(--panel)", border: "1px solid var(--line)",
  borderRadius: 7, color: "var(--t1)", outline: "none",
} as const;
// (field labels use the ui-kit `.vx-label`: sentence case, no uppercase eyebrow — guidelines §2.2)

/** THE BRIEF (prep-v3, owner-locked): the workspace README rendered as the page's stage — ONE doc,
 *  team-facing, always the next occurrence's brief. A SEEDED stub never renders as content (bloat
 *  law 3); missing/seeded → honest empty state whose CTA names its output. The interview itself
 *  happens in the chat rail (no question UI here) — this block is the RESULT of that dialogue. */
// slug-aware doc tab (same shape as workspace.tsx docTab): opens a file from the BOUND workspace.
const wsDocTab = (slug: string, path: string, title?: string) =>
  ({ id: `doc:${slug}:${path}`, title: title ?? (path.split("/").pop() ?? path), kind: "doc", params: { path, slug } });

const SEED_README_MARK = "This is your **Personal workspace**";

function Brief({ slug, title }: { slug: string; title: string }) {
  const layout = useService(LayoutServiceId);
  const [text, setText] = useState<string | null>(null);
  const [state, setState] = useState<"readme" | "none" | "loading">("loading");
  useEffect(() => {
    let alive = true;
    setState("loading");
    void readWorkspaceFile("README.md", { slug })
      .then((t) => {
        if (!alive) return;
        // a fresh workspace's seeded README is system exhaust, not a brief
        if (!t || !t.trim() || t.slice(0, 400).includes(SEED_README_MARK)) setState("none");
        else { setText(t); setState("readme"); }
      })
      .catch(() => { if (alive) setState("none"); });
    return () => { alive = false; };
  }, [slug]);
  const askForBrief = () => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, {
    detail: { prompt: `Prepare the brief for "${title}" in the ${slug} workspace README — who's attending (research them in our records), what happened last time, open follow-ups, and a suggested agenda. Ask me what you can't know from records.` },
  }));
  if (state === "loading") return null;
  if (state === "none" || !text) {
    return (
      <div className="mt-4 mr-0 mb-0 ml-0 pt-3 pr-3 pb-3 pl-3 r-lg t-xs c-3" style={{ border: "1px dashed var(--line2)", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ flex: 1, minWidth: 200 }}>No brief yet.</span>
        <button onClick={askForBrief} className="bg-accent-tint c-accent bd-none r-md pt-1 pr-3 pb-1 pl-3 t-xs fw-600" style={{ cursor: "pointer", flex: "none" }}>
          Draft the brief — attendees, last time, open items, agenda
        </button>
      </div>
    );
  }
  return (
    <div className="mt-4 mr-0 mb-0 ml-0 bd r-lg bg-2">
      <div className="pt-2 pr-3 pb-0 pl-3" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className="r-full bg-success" style={{ width: 6, height: 6, flex: "none" }} />
        <span className="t-xs c-3 f-mono" style={{ letterSpacing: ".08em" }}>
          team brief · workspace README
        </span>
        <span style={{ flex: 1 }} />
        <button onClick={() => layout.openTab(wsDocTab(slug, "README.md"))}
          title="Open as a document" className="bg-none bd-none c-3 t-xs p-0" style={{ cursor: "pointer" }}>
          open
        </button>
        <button onClick={askForBrief} title="Steer the brief in chat"
          className="bg-none bd-none c-3 t-xs p-0" style={{ cursor: "pointer" }}>
          update via chat
        </button>
      </div>
      <div className="pt-1 pr-4 pb-3 pl-4" style={{ maxHeight: 460, overflow: "auto" }}>
        <MdxDoc style={{ fontSize: 13, lineHeight: 1.55 }}>{text}</MdxDoc>
      </div>
    </div>
  );
}

/** Own-workspace brief (frame 6, owner-ruled 2026-07-09): with NO shared workspace bound, the brief
 *  is this meeting's note in the user's OWN workspace — and the prep page renders it LIVE while the
 *  brief chat writes it, instead of sitting on the "No brief yet" state. Polls the own-workspace
 *  tree (short interval while the tab is up — agents write mid-chat) and re-reads the note. */
const OWN_BRIEF_POLL_MS = 12_000;

function useOwnBriefNote(enabled: boolean, title: string, nativeId?: string | null) {
  const [note, setNote] = useState<{ path: string; text: string } | null>(null);
  useEffect(() => {
    if (!enabled) { setNote(null); return; }
    let alive = true;
    const look = async () => {
      try {
        const files = await listWorkspaceTree();
        const path = findBriefNote(files, { title, nativeId });
        if (!path) { if (alive) setNote(null); return; }
        const text = await readWorkspaceFile(path);
        if (!alive) return;
        if (!text || !text.trim() || isExampleNote(text)) setNote(null);
        else setNote({ path, text });
      } catch { /* keep the last good render — a poll blip must not blank the brief */ }
    };
    void look();
    const t = setInterval(() => { if (document.visibilityState === "visible") void look(); }, OWN_BRIEF_POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [enabled, title, nativeId]);
  return note;
}

function OwnBrief({ note, onSteer }: { note: { path: string; text: string }; onSteer: () => void }) {
  const layout = useService(LayoutServiceId);
  return (
    <div className="mt-4 mr-0 mb-0 ml-0 bd r-lg bg-2">
      <div className="pt-2 pr-3 pb-0 pl-3" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className="r-full bg-success" style={{ width: 6, height: 6, flex: "none" }} />
        <span className="t-xs c-3 f-mono" style={{ letterSpacing: ".08em" }}>
          your brief · this meeting&rsquo;s note
        </span>
        <span style={{ flex: 1 }} />
        <button onClick={() => layout.openTab({ id: `doc:${note.path}`, title: note.path.split("/").pop() ?? note.path, kind: "doc", params: { path: note.path } })}
          title="Open as a document" className="bg-none bd-none c-3 t-xs p-0" style={{ cursor: "pointer" }}>
          open
        </button>
        <button onClick={onSteer} title="Steer the brief in chat"
          className="bg-none bd-none c-3 t-xs p-0" style={{ cursor: "pointer" }}>
          update via chat
        </button>
      </div>
      <div className="pt-1 pr-4 pb-3 pl-4" style={{ maxHeight: 460, overflow: "auto" }}>
        <MdxDoc style={{ fontSize: 13, lineHeight: 1.55 }}>{note.text}</MdxDoc>
      </div>
    </div>
  );
}

// A synthetic empty PLANNED meeting for a DRAFT tab — no backend row yet. Idle intent so the prep
// form renders; every field empty. Replaced by the real row the moment lazy-create fires.
const DRAFT_M: MeetingMock = {
  id: "", title: "", when: "", status: "past", live_status: "idle", platform: "Google Meet",
  participants: [], mentioned: [], actions: [], transcript: [], insights: [],
};

function MeetingPrepTab({ params }: TabProps) {
  const layout = useService(LayoutServiceId);
  const all = useLiveMeetings();
  const meetingId = String(params.meetingId ?? "");
  const isDraft = !meetingId && !!params.draft;   // "+ Plan a meeting" tab, no row created yet
  const found: MeetingMock | undefined = meetingId ? all.find((x) => x.id === meetingId) : undefined;
  // In a draft tab, render against the empty placeholder until the row is created (then we hand off
  // to the canonical prep:<id> tab). A real tab whose row hasn't loaded yet shows "Loading…" below.
  const m: MeetingMock | undefined = found ?? (isDraft ? DRAFT_M : undefined);
  const readOnly = !!m?.shared;
  const isIntent = m?.live_status === "idle" || m?.live_status === "scheduled";

  const [title, setTitle] = useState("");
  const [link, setLink] = useState("");
  // lazy row creation: a draft creates its backend row on the FIRST real input, passing whatever the
  // user has typed, then hands the tab off to prep:<id>. `creating` guards against a double-create
  // from two near-simultaneous inputs; `mounted` guards setState after the handoff unmounts us.
  const creating = useRef(false);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);
  const ensureRow = async (): Promise<string | null> => {
    if (meetingId) return meetingId;               // already a real row
    if (creating.current) return null;             // a create is already in flight
    creating.current = true;
    const row = await createPlannedMeeting({
      title: title.trim() || undefined,
      meeting_url: link.trim() || undefined,
    });
    const id = String(row.id);
    refreshMeetings();
    // hand off: the canonical prep tab owns the created meeting; the draft tab closes.
    layout.openTab(prepTabDescriptor({ id, title: title.trim() || "New meeting" }));
    layout.closeTab(PREP_DRAFT_TAB_ID);
    return id;
  };
  // seed marker is the MEETING ID, not a boolean: the shared preview panel swaps params to a
  // DIFFERENT meeting without remounting — a boolean kept the previous meeting's title/link on
  // screen, and a blur would PATCH them onto the wrong row (observed live 2026-07-08).
  const [seededFor, setSeededFor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // A refused Join renders as its own in-flow panel, not as the generic one-line error.
  const [denial, setDenial] = useState<ServiceDenialPresentation | null>(null);
  const [shares, setShares] = useState<Membership[]>([]);
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);       // ⋯ row: link edit / unbind / delete
  const [rebindOpen, setRebindOpen] = useState(false);   // "change" → the bind select
  const [briefChatStarted, setBriefChatStarted] = useState(false); // own-brief interview dispatched

  // seed the form once PER MEETING (live refreshes must not clobber in-progress edits; a
  // preview swap to another meeting must re-seed)
  useEffect(() => {
    if (!m || seededFor === m.id) return;
    setTitle(m.title_custom ?? "");
    setLink(m.meeting_url ?? "");
    setSeededFor(m.id);
  }, [m, seededFor]);

  useEffect(() => {
    void listSharedMemberships()
      .then((ms) => setShares(ms.filter((s) => s.role === "owner" || s.role === "contributor")))
      .catch(() => {});
  }, []);

  const patch = async (body: Parameters<typeof updatePlannedMeeting>[1]) => {
    if (!m || readOnly) return;
    setBusy(true); setErr(null);
    try {
      const id = await ensureRow();               // draft → create the row first (with title/link)
      if (!id) return;
      await updatePlannedMeeting(id, body); refreshMeetings();
    }
    catch (e) { if (mounted.current) setErr(presentError(e).headline); }
    finally { if (mounted.current) setBusy(false); }
  };

  const sendNow = async () => {
    if (!m?.native_id) return;
    setBusy(true); setErr(null); setDenial(null);
    try {
      const platformSlug = m.platform === "Google Meet" ? "google_meet" : m.platform.toLowerCase().replace(/\s+/g, "_");
      const r = await fetch("/api/bots", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: platformSlug, native_meeting_id: m.native_id, ...(m.meeting_url ? { meeting_url: m.meeting_url } : {}), bot_name: defaultBotName() }),
      });
      // The body used to be read as raw TEXT and rethrown as a bare Error, so a denial payload
      // reached the presenter JSON-shaped and came out as "Something went wrong". Read it as the
      // typed ApiError instead, so 403 service_not_allowed can be told from a permissions fault.
      if (!r.ok) throw await readApiFailure(r, "/api/bots");
      refreshMeetings();
    } catch (e) {
      const state = resolveJoinError(e);
      if (state.kind === "denial") setDenial(state.presentation);
      else setErr(state.headline);
    }
    finally { setBusy(false); }
  };

  const share = async () => {
    if (!m?.workspace_id) return;
    setBusy(true); setErr(null);
    try {
      const inv = await mintInvite({ workspace_id: m.workspace_id, role: "contributor", mode: "open", expires_in_sec: 7 * 86400, max_uses: 50 });
      setInviteLink(`${window.location.origin}/?invite=${inv.token}`);
    } catch (e) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  const createAndBind = async () => {
    if (!m) return;
    setBusy(true); setErr(null);
    try {
      const id = await ensureRow();               // draft → create the row first
      if (!id) return;
      const name = (m.title_custom || title || "meeting-prep").slice(0, 60);
      const ws = await createSharedWorkspace(name);
      await updatePlannedMeeting(id, { workspace_id: ws.workspace_id });
      refreshMeetings();
      // Creating the shared workspace IS a scaffolding run (prep-v3 ruling, owner 2026-07-09):
      // the agent must know the workspace is brand-new and exists to collaborate with the other
      // participants around this meeting — its README is the team brief. Any brief already built
      // in the user's own workspace is the starting point, not discarded.
      const carry = ownBrief
        ? ` I already have a brief for this meeting in my own workspace at ${ownBrief.path} — carry its content over as the starting brief, then keep improving it.`
        : "";
      window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, {
        detail: { prompt: `I just created the shared workspace "${ws.workspace_id}" for the meeting "${m.title_custom || title || headline}" — it is brand-new (empty) and exists so the other participants and I can collaborate on this meeting and its series. Scaffold it now: write its README as the team brief (audience = everyone in the room, so keep my private context out unless I confirm it), set up whatever structure the series needs, and interview me for what you can't know from my records.${carry} Write early and keep updating as we talk — the README renders live on the meeting page.` },
      }));
    } catch (e) { if (mounted.current) setErr(presentError(e).headline); }
    finally { if (mounted.current) setBusy(false); }
  };

  // Frame 6 (first-run-onboarding): brief WITHOUT sharing — the agent interviews the user in
  // chat (the prep tab's meeting grounding rides the turn) and writes the brief into their OWN
  // workspace, so it follows the series without creating a shared space. Starting the brief is a real
  // commitment → create the draft's row first (so the note keys to a real meeting and reuses across it).
  const startBriefChat = async () => {
    setBusy(true); setErr(null);
    try {
      const id = await ensureRow();
      if (!id) return;
      // re-read the row so the note filename can key to its native id if a link was set
      const row = all.find((x) => x.id === id);
      const name = row?.title_custom || title || "this meeting";
      const nid = row?.native_id;
      const key = nid ? ` Name the note's file so it includes the meeting id ${nid} (kg/entities/meeting/…${nid}….md) — the meeting page finds and renders it by that id.` : "";
      setBriefChatStarted(true);
      window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, {
        detail: { prompt: `Interview me to build the brief for "${name}" — ask what you can't know from my records (who's in the room, what I want out of it, what the notetaker should listen for), research the attendees in my knowledge and public sources, then write the brief as this meeting's note in my own workspace (no shared workspace) so it can be reused across this meeting's series.${key} Write the note EARLY and keep updating it as we talk — it renders live on the meeting page.` },
      }));
    } catch (e) { if (mounted.current) setErr(presentError(e).headline); }
    finally { if (mounted.current) setBusy(false); }
  };

  const remove = async () => {
    if (!m) return;
    if (isDraft) { layout.closeTab(PREP_DRAFT_TAB_ID); return; }   // nothing persisted — just discard the draft
    setConfirmDelete(true);
  };
  // DELETE ASKS IN A DIALOG, NOT window.confirm (guidelines §4.11, S7): the button names the act.
  const [confirmDelete, setConfirmDelete] = useState(false);
  const doRemove = async () => {
    if (!m) return;
    setConfirmDelete(false);
    setBusy(true);
    try { await deletePlannedMeeting(m.id); refreshMeetings(); layout.closeTab(`prep:${m.id}`); }
    catch (e) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  const autoJoin = m?.auto_join !== false;   // absent = ON
  const headline = useMemo(() => m?.title_custom || m?.title || "Planned meeting", [m]);
  // own-workspace brief (frame 6): only hunted while the meeting is unbound — a bound workspace's
  // README is the brief and wins.
  // No brief hunt on a draft (no row/title yet) — it would match nothing or the wrong note.
  const ownBrief = useOwnBriefNote(!isDraft && !!m && !m.workspace_id && !readOnly && isIntent, headline, m?.native_id);

  // B2 tab hygiene: a calendar sweep DELETES + recreates rows (new ids) — a tab keyed to the old
  // row would dangle on "Loading meeting…" forever. Once the store has data and the row is gone,
  // the tab closes itself (the meeting lives on under its new row, reachable from Today).
  // Grace: only self-close a row we have ACTUALLY SEEN (then lost). A just-created tab whose row
  // hasn't loaded into the store yet (the draft → prep:<id> hand-off) must NOT close — it loads in.
  const everSeen = useRef(false);
  useEffect(() => { if (found) everSeen.current = true; }, [found]);
  const rowGone = !isDraft && !!meetingId && everSeen.current && all.length > 0 && !found;
  useEffect(() => {
    if (rowGone) layout.closeTab(`prep:${meetingId}`);
  }, [rowGone, layout, meetingId]);

  if (!m) {
    return <div className="p-8 t-sm c-3">Loading meeting…</div>;
  }
  if (!isIntent) {
    return (
      <div className="p-8 t-sm c-2 lh-normal">
        This meeting has started — open it from the Meetings list to see the live view.
      </div>
    );
  }

  return (
    <div className="pt-6 pr-6 pb-6 pl-6" style={{ width: "100%", height: "100%", overflow: "auto", boxSizing: "border-box" }}>
      <ConfirmDialog open={confirmDelete} title="Delete this planned meeting?" confirmLabel="Delete meeting"
        consequence="This planned meeting is removed from your meetings." busy={busy}
        onCancel={() => setConfirmDelete(false)} onConfirm={() => void doRemove()} />
      <div style={{ maxWidth: 640 }}>
        {/* TITLE-FIRST hero (prep-v3 carve): no status pills — the page you're on IS the state.
            Title editable in place, honest placeholder, never the "platform · (no link)" fallback. */}
        {readOnly ? (
          <h2 className="mt-0 mr-0 mb-4 ml-0 t-xl fw-600 c-1">{headline}</h2>
        ) : (
          <input value={title} disabled={busy} placeholder="What's this meeting about?"
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => { if ((m.title_custom ?? "") !== title.trim()) void patch({ title: title.trim() || null }); }}
            onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
            className="mt-0 mr-0 mb-4 ml-0 pt-0_5 pr-0 pb-1_5 pl-0 t-xl fw-600 c-1 bg-none bd-none" style={{ display: "block", width: "100%", boxSizing: "border-box", borderBottom: "1px dashed var(--line2)", outline: "none" }} />
        )}

        {m.auto_join_error && (
          <div role="alert" className="mt-0 mr-0 mb-3 ml-0 pt-2 pr-3 pb-2 pl-3 r-md bg-danger-tint c-danger t-xs lh-snug">
            ⚠ Auto-join failed: {m.auto_join_error}
          </div>
        )}

        {/* ── meta line (prep-v3 carve): when · Join · auto-join — the raw URL lives behind ⋯ ── */}
        <div className="mb-1_5" style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <DateTimePicker
            value={m.scheduled_at}
            disabled={readOnly || busy}
            placeholder="Pick a date & time"
            onChange={(iso) => void patch({ scheduled_at: iso })}
            onClear={() => void patch({ scheduled_at: null })}
          />
          {m.meeting_url && (
            /* "Open meeting" not "Join" — the human opens the URL; the notetaker is a separate verb
               (first-run-onboarding frame 6: Join/Send-bot/Auto-join read as flavors of one verb). */
            <a href={m.meeting_url} target="_blank" rel="noreferrer"
              className="bg-accent c-on-accent r-md pt-1 pr-3 pb-1 pl-3 t-xs fw-600" style={{ textDecoration: "none" }}>
              Open meeting
            </a>
          )}
          {!readOnly && (
            <label className="t-xs c-2" style={{ display: "inline-flex", alignItems: "center", gap: 7, cursor: "pointer", userSelect: "none" }}>
              <input type="checkbox" checked={autoJoin} disabled={busy}
                onChange={(e) => void patch({ auto_join: e.target.checked })} />
              Auto-join{!m.native_id && <span className="c-3 t-xs">(needs a link)</span>}
            </label>
          )}
        </div>
        {/* no link yet → the input is the honest primary control; with a link it lives in ⋯ */}
        {!readOnly && (!m.meeting_url || moreOpen) && (
          <div className="mt-2 mr-0 mb-1 ml-0" style={{ display: "flex", flexDirection: "column", gap: 4, maxWidth: 420 }}>
            <span className="vx-label">Meeting link</span>
            <input value={link} disabled={busy} placeholder="https://meet.google.com/…"
              onChange={(e) => setLink(e.target.value)}
              onBlur={() => { if ((m.meeting_url ?? "") !== link.trim()) void patch({ meeting_url: link.trim() || null }); }}
              style={field} />
          </div>
        )}

        {/* ── attendees (calendar ATTENDEE lines → data.attendees, prep-v3 slice b) ── */}
        {(m.attendees?.length ?? 0) > 0 && (
          <div className="mt-0 mr-0 mb-5 ml-0">
            <div className="mt-0 mr-0 mb-2 ml-0" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="vx-label">Attendees</span>
              <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
            </div>
            <div style={{ display: "flex", gap: 7, flexWrap: "wrap", alignItems: "center" }}>
              {m.attendees!.map((a) => {
                const display = a.name || a.email;
                const initials = (a.name
                  ? a.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("")
                  : a.email.slice(0, 2)).toUpperCase();
                const declined = a.partstat === "declined";
                return (
                  <span key={a.email} title={a.email + (a.partstat ? ` · ${a.partstat}` : "")}
                    className="bd r-xl pt-0_5 pr-3 pb-0_5 pl-0_5 t-xs" style={{ display: "inline-flex", alignItems: "center", gap: 7, color: declined ? "var(--t3)" : "var(--t1)", textDecoration: declined ? "line-through" : undefined }}>
                    <span className="r-full bg-3 c-2 t-xs fw-600" style={{ width: 19, height: 19, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>{initials}</span>
                    {display}
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {/* ── ONE quiet utility row (prep-v3 carve): workspace as a word · share · send · ⋯ ──
            Moved to the TOP (owner ruling): the actions sit above the brief so a long brief never
            buries them. The popouts (rebind select, ⋯ block, invite link) render right here. */}
        <div className="mb-3 pb-3 bd-b t-xs c-3" style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          {m.workspace_id && (
            <span style={{ display: "inline-flex", gap: 6, alignItems: "baseline" }}>
              workspace{" "}
              <button onClick={() => layout.openTab(manageTabDescriptor(m.workspace_id!, { shared: true }))}
                title={m.workspace_id}
                className="bg-none bd-none c-2 fw-600 t-xs p-0" style={{ cursor: "pointer" }}>
                {m.workspace_id.replace(/-[0-9a-f]{4,}$/i, "")}
              </button>
              {!readOnly && (
                <button onClick={() => setRebindOpen((v) => !v)}
                  className="bg-none bd-none c-3 t-xs p-0" style={{ cursor: "pointer", borderBottom: "1px dotted var(--t3)" }}>
                  change
                </button>
              )}
            </span>
          )}
          {/* an own brief (or one in flight) must NOT hide the sharing path — the no-brief
              placeholder's share button vanished exactly when there was something worth sharing
              (owner ruling 2026-07-09) */}
          {!readOnly && !m.workspace_id && (ownBrief || briefChatStarted) && (
            <button disabled={busy} onClick={() => void createAndBind()}
              title="Everyone you invite sees the brief and the live transcript the moment they join"
              className="bg-none bd-none c-2 t-xs p-0" style={{ cursor: "pointer", borderBottom: "1px dotted var(--t3)" }}>
              + Create a workspace to share
            </button>
          )}
          {readOnly && <span className="t-xs">shared with you</span>}
          <span style={{ flex: 1 }} />
          {!readOnly && m.workspace_id && (
            <button disabled={busy} onClick={() => void share()}
              className="bg-none bd-none c-2 t-xs p-0" style={{ cursor: "pointer", borderBottom: "1px dotted var(--t3)" }}>
              Share with attendees{(m.attendees?.length ?? 0) > 0 ? ` (${m.attendees!.length})` : ""}
            </button>
          )}
          {!readOnly && (
            <button disabled={busy || !m.native_id} onClick={() => void sendNow()}
              title={m.native_id ? "Send the bot now instead of waiting" : "Attach a meeting link first"}
              className="bg-none bd-none t-xs fw-600 p-0" style={{ color: m.native_id ? "var(--accent)" : "var(--t3)", cursor: m.native_id ? "pointer" : "default", borderBottom: `1px dotted ${m.native_id ? "var(--accent)" : "var(--t3)"}` }}>
              Send notetaker now
            </button>
          )}
          {!readOnly && (
            <button onClick={() => setMoreOpen((v) => !v)} title="More — edit link, unbind, delete"
              className="bg-none bd-none c-3 t-md pt-0 pr-0_5 pb-0 pl-0_5" style={{ cursor: "pointer" }}>
              ⋯
            </button>
          )}
        </div>
        {rebindOpen && !readOnly && shares.length > 0 && (
          <div className="mb-2">
            <select defaultValue="" disabled={busy}
              onChange={(e) => { if (e.target.value) { void patch({ workspace_id: e.target.value }); setRebindOpen(false); } }}
              style={{ ...field, minWidth: 200 }}>
              <option value="" disabled>Bind a different workspace…</option>
              {shares.map((s) => <option key={s.workspace_id} value={s.workspace_id}>{s.workspace_id}</option>)}
            </select>
          </div>
        )}
        {moreOpen && !readOnly && (
          <div className="mb-2" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {m.workspace_id && (
              <button disabled={busy} onClick={() => { void patch({ workspace_id: null }); setMoreOpen(false); }}
                className="bg-none bd-strong c-3 r-md pt-1 pr-2 pb-1 pl-2 t-xs" style={{ cursor: "pointer" }}>
                Unbind workspace
              </button>
            )}
            <button disabled={busy} onClick={() => void remove()}
              className="bg-none bd-strong c-danger r-md pt-1 pr-2 pb-1 pl-2 t-xs" style={{ cursor: "pointer" }}>
              Delete meeting
            </button>
          </div>
        )}
        {inviteLink && (
          <div className="mb-2" style={{ display: "flex", gap: 6 }}>
            <input readOnly value={inviteLink} onFocus={(e) => e.currentTarget.select()} className="t-xs" style={{ ...field, flex: 1 }} />
            <button onClick={() => void copyText(inviteLink)} className="t-xs pt-1 pr-3 pb-1 pl-3 bg-accent bd-none r-md" style={{ color: "var(--bg)", cursor: "pointer" }}>Copy</button>
          </div>
        )}

        {/* ── the brief = the stage (prep-v3 carve) ───────────────── */}
        {m.workspace_id ? (
          <Brief slug={m.workspace_id} title={headline} />
        ) : readOnly ? (
          <div className="mt-4 mr-0 mb-0 ml-0 t-xs c-3">No workspace bound.</div>
        ) : ownBrief ? (
          /* the own-workspace note IS the brief — rendered live while the chat writes it */
          <OwnBrief note={ownBrief} onSteer={() => startBriefChat()} />
        ) : briefChatStarted ? (
          <div className="mt-4 mr-0 mb-0 ml-0 pt-3 pr-3 pb-3 pl-3 r-lg t-xs c-3" style={{ border: "1px dashed var(--line2)", display: "flex", alignItems: "center", gap: 10 }}>
            <span className="r-full bg-accent" style={{ width: 6, height: 6, flex: "none" }} />
            Brief chat running — the brief renders here as the agent writes it.
          </div>
        ) : (
          /* No-brief state (first-run-onboarding frame 6, owner-ruled): two REAL actions. Brief chat
             = the agent interviews you here (the prep tab's meeting grounding rides the turn) and the
             brief lands in YOUR default workspace, reused across the series. Shared workspace = the
             existing collaborative flow, repositioned as the explicit sharing choice. */
          <div className="mt-4 mr-0 mb-0 ml-0 pt-3 pr-3 pb-3 pl-3 r-lg" style={{ border: "1px dashed var(--line2)", display: "flex", flexDirection: "column", gap: 9 }}>
            <div className="t-sm fw-600 c-1">No brief yet</div>
            <div className="t-xs c-3 lh-normal">
              A brief makes the notetaker useful — who&rsquo;s in the room, what you want out of it, what
              to listen for.
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button disabled={busy} onClick={() => startBriefChat()}
                className="bg-accent c-on-accent bd-none r-md pt-1_5 pr-3 pb-1_5 pl-3 t-xs fw-600" style={{ cursor: "pointer" }}>
                Start brief chat
              </button>
              <button disabled={busy} onClick={() => void createAndBind()}
                className="bg-none c-accent r-md pt-1 pr-3 pb-1 pl-3 t-xs fw-600" style={{ border: "1px dashed var(--line2)", cursor: "pointer" }}>
                + Create a workspace to share
              </button>
              {shares.length > 0 && (
                <select defaultValue="" disabled={busy} onChange={(e) => { if (e.target.value) void patch({ workspace_id: e.target.value }); }}
                  className="c-3" style={{ ...field, minWidth: 180 }}>
                  <option value="" disabled>or bind an existing one…</option>
                  {shares.map((s) => <option key={s.workspace_id} value={s.workspace_id}>{s.workspace_id}</option>)}
                </select>
              )}
            </div>
            <div className="t-xs c-3 lh-snug">
              <b className="c-2 fw-600">Brief chat</b> — the agent interviews you and
              writes the brief into your own workspace, reused across this series.{" "}
              <b className="c-2 fw-600">Shared workspace</b> — everyone you invite sees
              the brief and the live transcript the moment they join.
            </div>
          </div>
        )}

        {denial && <ServiceDenialPanel presentation={denial} onRetry={() => void sendNow()} />}
        {err && <div role="alert" className="mt-3 t-xs c-danger">⚠ {err}</div>}
      </div>
    </div>
  );
}

export const prepTabDescriptor = (m: { id: string; title: string }) =>
  ({ id: `prep:${m.id}`, title: m.title, kind: "meetingPrep", params: { meetingId: m.id } });

// A DRAFT "+ Plan a meeting" tab — no backend row yet. The row is created lazily on the first real
// input (title/link/date, or Start brief chat / Create workspace / bind), then the tab hands off to
// the canonical prep:<id> tab. Abandoning a draft leaves NO empty meeting behind. Stable id so
// repeated "+ Plan a meeting" clicks reuse the one open draft rather than stacking blank tabs.
export const PREP_DRAFT_TAB_ID = "prep:draft";
export const prepDraftTabDescriptor = () =>
  ({ id: PREP_DRAFT_TAB_ID, title: "New meeting", kind: "meetingPrep", params: { meetingId: "", draft: true } });

registerTab("meetingPrep", MeetingPrepTab);
