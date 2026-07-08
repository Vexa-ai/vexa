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
import { Markdown } from "../ui-kit/Markdown";
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
      <div style={{ margin: "18px 0 0", padding: "12px 14px", border: "1px dashed var(--line2)", borderRadius: 10, fontSize: 12.5, color: "var(--t3)", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ flex: 1, minWidth: 200 }}>No brief yet.</span>
        <button onClick={askForBrief} style={{ background: "var(--accentbg)", color: "var(--accent)", border: "none", borderRadius: 7, padding: "5px 12px", fontSize: 12, fontWeight: 600, cursor: "pointer", flex: "none" }}>
          Draft the brief — attendees, last time, open items, agenda
        </button>
      </div>
    );
  }
  return (
    <div style={{ margin: "18px 0 0", border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 14px 0" }}>
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--green)", flex: "none" }} />
        <span style={{ fontSize: 10, color: "var(--t3)", letterSpacing: ".08em", fontFamily: "var(--mono)" }}>
          team brief · workspace README
        </span>
        <span style={{ flex: 1 }} />
        <button onClick={() => layout.openTab(wsDocTab(slug, "README.md"))}
          title="Open as a document" style={{ background: "none", border: "none", color: "var(--t3)", fontSize: 11, cursor: "pointer", padding: 0 }}>
          open
        </button>
        <button onClick={askForBrief} title="Steer the brief in chat"
          style={{ background: "none", border: "none", color: "var(--t3)", fontSize: 11, cursor: "pointer", padding: 0 }}>
          update via chat
        </button>
      </div>
      <div style={{ padding: "4px 16px 12px", maxHeight: 460, overflow: "auto" }}>
        <Markdown style={{ fontSize: 13, lineHeight: 1.55 }}>{text}</Markdown>
      </div>
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
  const [moreOpen, setMoreOpen] = useState(false);       // ⋯ row: link edit / unbind / delete
  const [rebindOpen, setRebindOpen] = useState(false);   // "change" → the bind select

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
        {/* TITLE-FIRST hero (prep-v3 carve): no status pills — the page you're on IS the state.
            Title editable in place, honest placeholder, never the "platform · (no link)" fallback. */}
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

        {/* ── meta line (prep-v3 carve): when · Join · auto-join — the raw URL lives behind ⋯ ── */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 6 }}>
          <DateTimePicker
            value={m.scheduled_at}
            disabled={readOnly || busy}
            placeholder="Pick a date & time"
            onChange={(iso) => void patch({ scheduled_at: iso })}
            onClear={() => void patch({ scheduled_at: null })}
          />
          {m.meeting_url && (
            <a href={m.meeting_url} target="_blank" rel="noreferrer"
              style={{ background: "var(--accent)", color: "var(--on-accent)", borderRadius: 7, padding: "5px 14px", fontSize: 12.5, fontWeight: 600, textDecoration: "none" }}>
              Join
            </a>
          )}
          {!readOnly && (
            <label style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 12, color: "var(--t2)", cursor: "pointer", userSelect: "none" }}>
              <input type="checkbox" checked={autoJoin} disabled={busy}
                onChange={(e) => void patch({ auto_join: e.target.checked })} />
              Auto-join{!m.native_id && <span style={{ color: "var(--t3)", fontSize: 11 }}>(needs a link)</span>}
            </label>
          )}
        </div>
        {/* no link yet → the input is the honest primary control; with a link it lives in ⋯ */}
        {!readOnly && (!m.meeting_url || moreOpen) && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, margin: "8px 0 4px", maxWidth: 420 }}>
            <span style={label}>Meeting link</span>
            <input value={link} disabled={busy} placeholder="https://meet.google.com/…"
              onChange={(e) => setLink(e.target.value)}
              onBlur={() => { if ((m.meeting_url ?? "") !== link.trim()) void patch({ meeting_url: link.trim() || null }); }}
              style={field} />
          </div>
        )}

        {/* ── attendees (calendar ATTENDEE lines → data.attendees, prep-v3 slice b) ── */}
        {(m.attendees?.length ?? 0) > 0 && (
          <div style={{ margin: "0 0 22px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 0 8px" }}>
              <span style={label}>Attendees</span>
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
                    style={{ display: "inline-flex", alignItems: "center", gap: 7,
                      border: "1px solid var(--line)", borderRadius: 14, padding: "2px 11px 2px 3px",
                      fontSize: 12.5, color: declined ? "var(--t3)" : "var(--t1)",
                      textDecoration: declined ? "line-through" : undefined }}>
                    <span style={{ width: 19, height: 19, borderRadius: "50%", background: "var(--panel2)",
                      color: "var(--t2)", display: "inline-flex", alignItems: "center", justifyContent: "center",
                      fontSize: 8.5, fontWeight: 700 }}>{initials}</span>
                    {display}
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {/* ── the brief = the stage (prep-v3 carve) ───────────────── */}
        {m.workspace_id ? (
          <Brief slug={m.workspace_id} title={headline} />
        ) : readOnly ? (
          <div style={{ margin: "18px 0 0", fontSize: 12.5, color: "var(--t3)" }}>No workspace bound.</div>
        ) : (
          <div style={{ margin: "18px 0 0", padding: "12px 14px", border: "1px dashed var(--line2)", borderRadius: 10, display: "flex", flexDirection: "column", gap: 9 }}>
            <div style={{ fontSize: 12.5, color: "var(--t3)", lineHeight: 1.55 }}>
              No brief yet — a prep workspace holds it, and everyone you share it with sees this
              meeting (and its live transcript) the moment they join.
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button disabled={busy} onClick={() => void createAndBind()}
                style={{ background: "var(--accentbg)", color: "var(--accent)", border: "none", borderRadius: 7, padding: "5px 12px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>
                + Create a prep workspace
              </button>
              {shares.length > 0 && (
                <select defaultValue="" disabled={busy} onChange={(e) => { if (e.target.value) void patch({ workspace_id: e.target.value }); }}
                  style={{ ...field, minWidth: 180, color: "var(--t3)" }}>
                  <option value="" disabled>or bind an existing one…</option>
                  {shares.map((s) => <option key={s.workspace_id} value={s.workspace_id}>{s.workspace_id}</option>)}
                </select>
              )}
            </div>
          </div>
        )}

        {inviteLink && (
          <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
            <input readOnly value={inviteLink} onFocus={(e) => e.currentTarget.select()} style={{ ...field, flex: 1, fontSize: 11.5 }} />
            <button onClick={() => void copyText(inviteLink)} style={{ fontSize: 12, padding: "4px 12px", background: "var(--accent)", color: "var(--bg)", border: "none", borderRadius: 7, cursor: "pointer" }}>Copy</button>
          </div>
        )}

        {/* ── ONE quiet utility row (prep-v3 carve): workspace as a word · share · send · ⋯ ── */}
        <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap", marginTop: 22, paddingTop: 12, borderTop: "1px solid var(--line)", fontSize: 12, color: "var(--t3)" }}>
          {m.workspace_id && (
            <span style={{ display: "inline-flex", gap: 6, alignItems: "baseline" }}>
              workspace{" "}
              <button onClick={() => layout.openTab(manageTabDescriptor(m.workspace_id!, { shared: true }))}
                title={m.workspace_id}
                style={{ background: "none", border: "none", color: "var(--t2)", fontWeight: 600, fontSize: 12, cursor: "pointer", padding: 0 }}>
                {m.workspace_id.replace(/-[0-9a-f]{4,}$/i, "")}
              </button>
              {!readOnly && (
                <button onClick={() => setRebindOpen((v) => !v)}
                  style={{ background: "none", border: "none", color: "var(--t3)", fontSize: 11.5, cursor: "pointer", padding: 0, borderBottom: "1px dotted var(--t3)" }}>
                  change
                </button>
              )}
            </span>
          )}
          {readOnly && <span style={{ fontSize: 11.5 }}>shared with you</span>}
          <span style={{ flex: 1 }} />
          {!readOnly && m.workspace_id && (
            <button disabled={busy} onClick={() => void share()}
              style={{ background: "none", border: "none", color: "var(--t2)", fontSize: 12, cursor: "pointer", padding: 0, borderBottom: "1px dotted var(--t3)" }}>
              Share with attendees{(m.attendees?.length ?? 0) > 0 ? ` (${m.attendees!.length})` : ""}
            </button>
          )}
          {!readOnly && (
            <button disabled={busy || !m.native_id} onClick={() => void sendNow()}
              title={m.native_id ? "Send the bot now instead of waiting" : "Attach a meeting link first"}
              style={{ background: "none", border: "none", color: m.native_id ? "var(--accent)" : "var(--t3)", fontSize: 12, fontWeight: 600, cursor: m.native_id ? "pointer" : "default", padding: 0, borderBottom: `1px dotted ${m.native_id ? "var(--accent)" : "var(--t3)"}` }}>
              Send bot now
            </button>
          )}
          {!readOnly && (
            <button onClick={() => setMoreOpen((v) => !v)} title="More — edit link, unbind, delete"
              style={{ background: "none", border: "none", color: "var(--t3)", fontSize: 14, cursor: "pointer", padding: "0 2px" }}>
              ⋯
            </button>
          )}
        </div>
        {rebindOpen && !readOnly && shares.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <select defaultValue="" disabled={busy}
              onChange={(e) => { if (e.target.value) { void patch({ workspace_id: e.target.value }); setRebindOpen(false); } }}
              style={{ ...field, minWidth: 200 }}>
              <option value="" disabled>Bind a different workspace…</option>
              {shares.map((s) => <option key={s.workspace_id} value={s.workspace_id}>{s.workspace_id}</option>)}
            </select>
          </div>
        )}
        {moreOpen && !readOnly && (
          <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
            {m.workspace_id && (
              <button disabled={busy} onClick={() => { void patch({ workspace_id: null }); setMoreOpen(false); }}
                style={{ background: "transparent", border: "1px solid var(--line2)", color: "var(--t3)", borderRadius: 7, padding: "4px 10px", fontSize: 12, cursor: "pointer" }}>
                Unbind workspace
              </button>
            )}
            <button disabled={busy} onClick={() => void remove()}
              style={{ background: "transparent", border: "1px solid var(--line2)", color: "var(--danger)", borderRadius: 7, padding: "4px 10px", fontSize: 12, cursor: "pointer" }}>
              Delete meeting
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
