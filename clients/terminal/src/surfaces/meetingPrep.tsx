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
import { useEffect, useMemo, useState } from "react";
import { registerTab, type TabProps } from "../contributions";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { Icon } from "../ui-kit";
import { DateTimePicker } from "../ui-kit/DateTimePicker";
import { copyText } from "../ui-kit/ContextMenu";
import { useLiveMeetings, refreshMeetings } from "./liveMeetings";
import type { MeetingMock } from "./meetingModel";
import { updatePlannedMeeting, deletePlannedMeeting } from "./plannedApi";
import { createSharedWorkspace, listSharedMemberships, mintInvite, readWorkspaceFile, type Membership } from "./workspaceApi";
import { manageTabDescriptor } from "./workspaceManage";
import { ASK_CHAT_EVENT } from "../canvas/actions";

const field = {
  fontSize: 12.5, padding: "6px 8px", background: "var(--panel)", border: "1px solid var(--line)",
  borderRadius: 7, color: "var(--t1)", outline: "none",
} as const;
const label = { fontSize: 10.5, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".06em", fontWeight: 600 } as const;

/** The PREP BRIEF (design-spec meeting-lifecycle-v2, W3): the agent-maintained doc this meeting's
 *  chat writes into. Prefers the meeting's own `meetings/{id}/prep.md` in the bound workspace, falls
 *  back to the workspace README (the shared context attendees land in), else offers the chat CTA. */
function PrepBrief({ slug, meetingId, title }: { slug: string; meetingId: string; title: string }) {
  const [text, setText] = useState<string | null>(null);
  const [source, setSource] = useState<"brief" | "readme" | "none" | "loading">("loading");
  useEffect(() => {
    let alive = true;
    void readWorkspaceFile(`meetings/${meetingId}/prep.md`, { slug })
      .then((t) => { if (alive) { setText(t); setSource("brief"); } })
      .catch(() => readWorkspaceFile("README.md", { slug })
        .then((t) => { if (alive) { setText(t); setSource("readme"); } })
        .catch(() => { if (alive) setSource("none"); }));
    return () => { alive = false; };
  }, [slug, meetingId]);
  const askForBrief = () => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, {
    detail: { prompt: `Draft a one-page prep brief for the meeting "${title}" into ${slug}:meetings/${meetingId}/prep.md — last touchpoints, open items, attendees worth researching, and a suggested agenda.` },
  }));
  if (source === "loading") return null;
  if (source === "none" || !text) {
    return (
      <div style={{ margin: "10px 0 0", padding: "10px 12px", border: "1px dashed var(--line2)", borderRadius: 8, fontSize: 12.5, color: "var(--t3)", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ flex: 1, minWidth: 200 }}>No brief yet — the agent can research this meeting into the workspace.</span>
        <button onClick={askForBrief} style={{ background: "var(--accentbg)", color: "var(--accent)", border: "none", borderRadius: 7, padding: "4px 11px", fontSize: 12, fontWeight: 600, cursor: "pointer", flex: "none" }}>
          Ask the agent to draft it
        </button>
      </div>
    );
  }
  return (
    <div style={{ margin: "10px 0 0", border: "1px solid var(--line)", borderLeft: "3px solid var(--accent)", borderRadius: 8, background: "var(--panel)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 12px 0" }}>
        <span style={{ fontSize: 10, color: "var(--accent)", textTransform: "uppercase", letterSpacing: ".1em", fontFamily: "var(--mono)" }}>
          {source === "brief" ? "prep brief" : "workspace readme"}
        </span>
        <span style={{ flex: 1 }} />
        <button onClick={askForBrief} title="Ask the agent to update the brief in chat"
          style={{ background: "none", border: "none", color: "var(--t3)", fontSize: 11, cursor: "pointer", padding: 0 }}>
          update via chat
        </button>
      </div>
      <pre style={{ margin: 0, padding: "6px 12px 10px", fontSize: 12, color: "var(--t2)", lineHeight: 1.5, maxHeight: 220, overflow: "auto", whiteSpace: "pre-wrap", fontFamily: "inherit" }}>
        {text.slice(0, 2000)}
      </pre>
    </div>
  );
}

function MeetingPrepTab({ params }: TabProps) {
  const layout = useService(LayoutServiceId);
  const all = useLiveMeetings();
  const meetingId = String(params.meetingId ?? "");
  const m: MeetingMock | undefined = all.find((x) => x.id === meetingId);
  const readOnly = !!m?.shared;
  const isIntent = m?.live_status === "idle" || m?.live_status === "scheduled";

  const [title, setTitle] = useState("");
  const [link, setLink] = useState("");
  // seed marker is the MEETING ID, not a boolean: the shared preview panel swaps params to a
  // DIFFERENT meeting without remounting — a boolean kept the previous meeting's title/link on
  // screen, and a blur would PATCH them onto the wrong row (observed live 2026-07-08).
  const [seededFor, setSeededFor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [shares, setShares] = useState<Membership[]>([]);
  const [inviteLink, setInviteLink] = useState<string | null>(null);

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
    try { await updatePlannedMeeting(m.id, body); refreshMeetings(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const sendNow = async () => {
    if (!m?.native_id) return;
    setBusy(true); setErr(null);
    try {
      const platformSlug = m.platform === "Google Meet" ? "google_meet" : m.platform.toLowerCase().replace(/\s+/g, "_");
      const r = await fetch("/api/bots", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: platformSlug, native_meeting_id: m.native_id, ...(m.meeting_url ? { meeting_url: m.meeting_url } : {}), bot_name: "Vexa" }),
      });
      if (!r.ok) throw new Error((await r.text().catch(() => "")).slice(0, 180) || `${r.status}`);
      refreshMeetings();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const share = async () => {
    if (!m?.workspace_id) return;
    setBusy(true); setErr(null);
    try {
      const inv = await mintInvite({ workspace_id: m.workspace_id, role: "contributor", mode: "open", expires_in_sec: 7 * 86400, max_uses: 50 });
      setInviteLink(`${window.location.origin}/?invite=${inv.token}`);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const createAndBind = async () => {
    if (!m) return;
    setBusy(true); setErr(null);
    try {
      const name = (m.title_custom || title || "meeting-prep").slice(0, 60);
      const ws = await createSharedWorkspace(name);
      await updatePlannedMeeting(m.id, { workspace_id: ws.workspace_id });
      refreshMeetings();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const remove = async () => {
    if (!m) return;
    if (typeof window !== "undefined" && !window.confirm("Delete this planned meeting?")) return;
    setBusy(true);
    try { await deletePlannedMeeting(m.id); refreshMeetings(); layout.closeTab(`prep:${m.id}`); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const autoJoin = m?.auto_join !== false;   // absent = ON
  const headline = useMemo(() => m?.title_custom || m?.title || "Planned meeting", [m]);

  if (!m) {
    return <div style={{ padding: 32, fontSize: 13, color: "var(--t3)" }}>Loading meeting…</div>;
  }
  if (!isIntent) {
    return (
      <div style={{ padding: 32, fontSize: 13, color: "var(--t2)", lineHeight: 1.6 }}>
        This meeting has started — open it from the Meetings list to see the live view.
      </div>
    );
  }

  return (
    <div style={{ width: "100%", height: "100%", overflow: "auto", boxSizing: "border-box", padding: "24px 28px" }}>
      <div style={{ maxWidth: 640 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
          <span style={{ fontSize: 11, color: "var(--accent)", background: "var(--accentbg)", borderRadius: 5, padding: "1px 7px", fontWeight: 600 }}>
            {m.live_status === "scheduled" ? "Scheduled" : "Planned"}
          </span>
          {m.calendar_uid && <span title="Imported from your calendar" style={{ fontSize: 10.5, color: "var(--t3)", border: "1px solid var(--line)", borderRadius: 5, padding: "0 6px", display: "inline-flex", alignItems: "center", gap: 4 }}><Icon name="cal" size={10} /> calendar</span>}
          {readOnly && <span style={{ fontSize: 10.5, color: "var(--t3)", border: "1px solid var(--line)", borderRadius: 5, padding: "0 6px" }}>shared with you</span>}
        </div>
        {/* TITLE-FIRST hero (design-spec W3): the title IS the headline — editable in place, honest
            placeholder, never the "platform · (no link)" fallback. */}
        {readOnly ? (
          <h2 style={{ margin: "0 0 18px", fontSize: 19, fontWeight: 650, color: "var(--t1)" }}>{headline}</h2>
        ) : (
          <input value={title} disabled={busy} placeholder="What's this meeting about?"
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => { if ((m.title_custom ?? "") !== title.trim()) void patch({ title: title.trim() || null }); }}
            onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
            style={{ display: "block", width: "100%", boxSizing: "border-box", margin: "0 0 18px", padding: "2px 0 6px",
              fontSize: 19, fontWeight: 650, color: "var(--t1)", background: "transparent", border: "none",
              borderBottom: "1px dashed var(--line2)", outline: "none" }} />
        )}

        {m.auto_join_error && (
          <div role="alert" style={{ margin: "0 0 14px", padding: "8px 12px", borderRadius: 8, background: "var(--dangerbg)", color: "var(--danger)", fontSize: 12.5, lineHeight: 1.5 }}>
            ⚠ Auto-join failed: {m.auto_join_error}
          </div>
        )}

        {/* ── details ─────────────────────────────────────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 22 }}>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: "none" }}>
              <span style={label}>When</span>
              <DateTimePicker
                value={m.scheduled_at}
                disabled={readOnly || busy}
                placeholder="Pick a date & time"
                onChange={(iso) => void patch({ scheduled_at: iso })}
                onClear={() => void patch({ scheduled_at: null })}
              />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 220 }}>
              <span style={label}>Meeting link</span>
              <input value={link} disabled={readOnly || busy} placeholder="https://meet.google.com/…"
                onChange={(e) => setLink(e.target.value)}
                onBlur={() => { if ((m.meeting_url ?? "") !== link.trim()) void patch({ meeting_url: link.trim() || null }); }}
                style={field} />
            </div>
          </div>
          {!readOnly && (
            <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--t2)", cursor: "pointer", userSelect: "none" }}>
              <input type="checkbox" checked={autoJoin} disabled={busy}
                onChange={(e) => void patch({ auto_join: e.target.checked })} />
              Auto-join — send the bot when the meeting starts
              {!m.native_id && <span style={{ color: "var(--t3)", fontSize: 11.5 }}>(needs a meeting link)</span>}
            </label>
          )}
        </div>

        {/* ── prep workspace = the sharing surface ────────────────── */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 0 10px" }}>
          <span style={label}>Prep workspace</span>
          <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
        </div>
        {m.workspace_id ? (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <button onClick={() => layout.openTab(manageTabDescriptor(m.workspace_id!, { shared: true }))}
                title="Open the workspace manage panel"
                style={{ display: "inline-flex", alignItems: "center", gap: 7, padding: "5px 10px", borderRadius: 8, background: "var(--panel)", border: "1px solid var(--line)", color: "var(--t1)", fontSize: 12.5, cursor: "pointer" }}>
                <Icon name="panel" size={12} /> {m.workspace_id}
              </button>
              {!readOnly && (
                <>
                  <button disabled={busy} onClick={() => void share()}
                    style={{ display: "inline-flex", alignItems: "center", gap: 5, background: "var(--accent)", color: "var(--bg)", border: "none", borderRadius: 7, padding: "5px 12px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>
                    <Icon name="upload" size={11} /> Share with attendees
                  </button>
                  <button disabled={busy} onClick={() => void patch({ workspace_id: null })}
                    style={{ background: "transparent", border: "1px solid var(--line2)", color: "var(--t3)", borderRadius: 7, padding: "4px 10px", fontSize: 12, cursor: "pointer" }}>
                    Unbind
                  </button>
                </>
              )}
            </div>
            {inviteLink && (
              <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
                <input readOnly value={inviteLink} onFocus={(e) => e.currentTarget.select()} style={{ ...field, flex: 1, fontSize: 11.5 }} />
                <button onClick={() => void copyText(inviteLink)} style={{ fontSize: 12, padding: "4px 12px", background: "var(--accent)", color: "var(--bg)", border: "none", borderRadius: 7, cursor: "pointer" }}>Copy</button>
              </div>
            )}
            <PrepBrief slug={m.workspace_id} meetingId={m.id} title={headline} />
          </>
        ) : readOnly ? (
          <div style={{ fontSize: 12.5, color: "var(--t3)" }}>No workspace bound.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ fontSize: 12.5, color: "var(--t3)", lineHeight: 1.55 }}>
              Bind a workspace to prepare context with the agent and share it with the people you&apos;re meeting —
              they see this meeting (and its live transcript) the moment they join the workspace.
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              {shares.length > 0 && (
                <select defaultValue="" disabled={busy} onChange={(e) => { if (e.target.value) void patch({ workspace_id: e.target.value }); }} style={{ ...field, minWidth: 200 }}>
                  <option value="" disabled>Bind an existing workspace…</option>
                  {shares.map((s) => <option key={s.workspace_id} value={s.workspace_id}>{s.workspace_id}</option>)}
                </select>
              )}
              <button disabled={busy} onClick={() => void createAndBind()}
                style={{ background: "var(--panel)", border: "1px solid var(--line2)", color: "var(--t1)", borderRadius: 7, padding: "5px 12px", fontSize: 12, fontWeight: 550, cursor: "pointer" }}>
                + Create a prep workspace
              </button>
            </div>
          </div>
        )}

        {/* ── actions ─────────────────────────────────────────────── */}
        {!readOnly && (
          <div style={{ display: "flex", gap: 8, marginTop: 26, alignItems: "center" }}>
            <button disabled={busy || !m.native_id} onClick={() => void sendNow()}
              title={m.native_id ? "Send the bot now instead of waiting" : "Attach a meeting link first"}
              style={{ background: m.native_id ? "var(--accent)" : "var(--panel2)", color: m.native_id ? "var(--bg)" : "var(--t3)", border: "none", borderRadius: 7, padding: "6px 14px", fontSize: 12.5, fontWeight: 600, cursor: m.native_id ? "pointer" : "default" }}>
              Send bot now
            </button>
            <span style={{ flex: 1 }} />
            <button disabled={busy} onClick={() => void remove()}
              style={{ background: "transparent", border: "1px solid var(--line2)", color: "var(--danger)", borderRadius: 7, padding: "5px 12px", fontSize: 12, cursor: "pointer" }}>
              Delete
            </button>
          </div>
        )}

        {err && <div role="alert" style={{ marginTop: 12, fontSize: 12, color: "var(--danger)" }}>⚠ {err}</div>}
      </div>
    </div>
  );
}

export const prepTabDescriptor = (m: { id: string; title: string }) =>
  ({ id: `prep:${m.id}`, title: m.title, kind: "meetingPrep", params: { meetingId: m.id } });

registerTab("meetingPrep", MeetingPrepTab);
