"use client";
/** Calendar connections — the Settings → Calendar body (#1150 plural API).
 *
 *  An account holds up to ten NAMED ICS feeds, each with its own enabled flag, auto-join policy and
 *  bot name. This is the durable management surface: list · add · edit · disconnect · sync-one. The
 *  Meetings sidebar keeps its own first-connect card at the point of need (`meetingsOnboarding`).
 *
 *  Three rules this surface exists to hold:
 *
 *  1. **The feed address is write-only.** It goes out on create/replace and never comes back: the
 *     API returns `ics_url_set` plus a recognition mask, and no input here is ever PREFILLED from
 *     the server. Replacing a feed means typing a new one into an empty (masked) field.
 *  2. **Disconnect states its consequence before it fires.** Removing a connection disarms the
 *     meetings it alone imported — the row says so, in place, and needs a second click.
 *  3. **The ten-connection cap is a UI fact, not a surprise 409.** The count is always visible and
 *     Add is refused locally at the cap (the backend 409 stays the real gate, and still surfaces).
 *
 *  Sync is not automatic on the backend after a write, so the client runs it: after a create, and
 *  after any edit whose effect must be reconciled onto already-imported meetings (auto-join, bot
 *  name, enabled, a replaced feed). A rename alone changes nothing downstream, so it skips the sync.
 */
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { Icon } from "../ui-kit";
import { presentError } from "./apiClient";
import { refreshMeetings } from "./liveMeetings";
import {
  MAX_CALENDARS, listCalendars, createCalendar, updateCalendar, deleteCalendar,
  syncCalendar, getCalendarSyncStatus,
  type CalendarConnection, type CalendarUpdateBody, type CalendarSyncStamp,
} from "./plannedApi";

const field: CSSProperties = { width: "100%", boxSizing: "border-box", fontSize: 12, padding: "6px 9px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)" };
const btn: CSSProperties = { fontSize: 12, padding: "5px 12px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)", cursor: "pointer" };
const primaryBtn: CSSProperties = { ...btn, background: "var(--accent)", color: "var(--on-accent)", border: "none" };
const row: CSSProperties = { border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px", display: "flex", flexDirection: "column", gap: 8 };
const meta: CSSProperties = { fontSize: 11, color: "var(--t3)", lineHeight: 1.5 };
const labelled: CSSProperties = { display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--t2)" };
const labelCol: CSSProperties = { width: 96, flex: "none", color: "var(--t3)" };
const checkRow: CSSProperties = { display: "flex", alignItems: "center", gap: 7, fontSize: 12, color: "var(--t2)", cursor: "pointer" };

/** Which PATCH fields must be reconciled onto already-imported meetings by a follow-up sync.
 *  A rename is cosmetic on the connection alone — everything else changes what gets joined. */
export function patchNeedsSync(body: CalendarUpdateBody): boolean {
  return body.auto_join !== undefined || body.bot_name !== undefined
    || body.enabled !== undefined || body.ics_url !== undefined;
}

/** The one-line state of a connection's last sync attempt. `last_error` REPLACES the timestamp —
 *  a 200 carrying an error is a failed sync, not a successful one with a footnote. */
export function syncLine(stamp: CalendarSyncStamp | undefined): { ok: boolean; text: string } {
  if (stamp?.last_error) return { ok: false, text: `Last sync failed: ${stamp.last_error}` };
  if (!stamp?.last_sync) return { ok: true, text: "Not synced yet" };
  const c = stamp.counts;
  const counts = c
    ? ` · ${c.created ?? 0} imported, ${c.updated ?? 0} updated${c.cancelled ? `, ${c.cancelled} cancelled` : ""}`
    : "";
  return { ok: true, text: `Synced ${new Date(stamp.last_sync).toLocaleString()}${counts}` };
}

/** What the feed field says. The stored address is NEVER rendered; the mask (host + suffix) is a
 *  recognition hint the API hands out precisely because it is not the credential. */
export function feedLine(cal: CalendarConnection): string {
  if (!cal.ics_url_set) return "No feed set";
  return cal.ics_url_masked ? `Feed set · ${cal.ics_url_masked}` : "Feed set";
}

/** The consequence sentence, said BEFORE the destructive click — not after it. */
export function disconnectWarning(name: string): string {
  return `Disconnect “${name}”? Meetings this calendar alone imported are removed from Meetings and will not be joined. Meetings from another calendar, and ones you planned by hand, stay. Its feed address is deleted.`;
}

function EditPanel({ cal, busy, onSave, onCancel }: {
  cal: CalendarConnection;
  busy: boolean;
  onSave: (body: CalendarUpdateBody) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(cal.name);
  const [botName, setBotName] = useState(cal.bot_name ?? "");
  const [icsUrl, setIcsUrl] = useState("");   // always starts EMPTY — nothing stored is echoed back

  const body: CalendarUpdateBody = {};
  if (name.trim() && name.trim() !== cal.name) body.name = name.trim();
  if (botName.trim() && botName.trim() !== (cal.bot_name ?? "")) body.bot_name = botName.trim();
  if (icsUrl.trim()) body.ics_url = icsUrl.trim();
  const dirty = Object.keys(body).length > 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7, borderTop: "1px dashed var(--line)", paddingTop: 9 }}>
      <label style={labelled}>
        <span style={labelCol}>Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} style={field} aria-label={`Name for ${cal.name}`} />
      </label>
      <label style={labelled}>
        <span style={labelCol}>Bot name</span>
        <input value={botName} onChange={(e) => setBotName(e.target.value)} maxLength={100} placeholder="Vexa"
          style={field} aria-label={`Bot name for ${cal.name}`} />
      </label>
      <label style={labelled}>
        <span style={labelCol}>Replace feed</span>
        <input value={icsUrl} onChange={(e) => setIcsUrl(e.target.value)} type="password" autoComplete="off"
          placeholder="paste a new secret ICS address to replace it" style={field}
          aria-label={`Replace feed address for ${cal.name}`} />
      </label>
      <div style={{ ...meta, paddingLeft: 104 }}>
        The stored address is never shown again. Leave this empty to keep the current feed.
      </div>
      <div style={{ display: "flex", gap: 8, paddingLeft: 104 }}>
        <button disabled={busy || !dirty} onClick={() => onSave(body)}
          style={{ ...(dirty ? primaryBtn : btn), opacity: busy || !dirty ? 0.5 : 1 }}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button disabled={busy} onClick={onCancel} style={btn}>Cancel</button>
      </div>
    </div>
  );
}

function CalendarRow({ cal, stamp, busy, onPatch, onSync, onDisconnect }: {
  cal: CalendarConnection;
  stamp: CalendarSyncStamp | undefined;
  busy: string | null;
  onPatch: (body: CalendarUpdateBody) => void;
  onSync: () => void;
  onDisconnect: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const locked = busy !== null;
  const sync = syncLine(stamp);

  return (
    <div style={{ ...row, opacity: cal.enabled ? 1 : 0.7 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Icon name="cal" size={13} style={{ color: cal.enabled ? "var(--green)" : "var(--t3)" }} />
        <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--t1)" }}>{cal.name}</span>
        <span style={{ flex: 1, fontSize: 11.5, color: "var(--t3)", fontFamily: "var(--mono)" }}>{feedLine(cal)}</span>
        <button disabled={locked} onClick={onSync} style={btn}>{busy === "sync" ? "Syncing…" : "Sync now"}</button>
        <button disabled={locked} onClick={() => { setEditing((v) => !v); setConfirming(false); }}
          aria-expanded={editing} style={btn}>{editing ? "Close" : "Edit"}</button>
        <button disabled={locked} onClick={() => { setConfirming(true); setEditing(false); }}
          style={{ ...btn, color: "var(--danger)" }}>Disconnect</button>
      </div>

      <div style={{ ...sync.ok ? meta : { ...meta, color: "var(--danger)" } }} role={sync.ok ? undefined : "alert"}>
        {sync.ok ? "" : "⚠ "}{sync.text}
      </div>

      <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
        <label style={checkRow}>
          <input type="checkbox" checked={cal.enabled} disabled={locked}
            onChange={(e) => onPatch({ enabled: e.target.checked })} />
          Enabled — keep checking this feed for new meetings
        </label>
        <label style={checkRow}>
          <input type="checkbox" checked={cal.auto_join} disabled={locked}
            onChange={(e) => onPatch({ auto_join: e.target.checked })} />
          Auto-join — send the bot to this calendar&rsquo;s meetings
        </label>
        <span style={meta}>Bot name <span style={{ color: "var(--t2)", fontFamily: "var(--mono)" }}>{cal.bot_name || "Vexa"}</span></span>
      </div>

      {confirming && (
        <div role="alert" style={{ display: "flex", flexDirection: "column", gap: 7, borderTop: "1px dashed var(--line)", paddingTop: 9 }}>
          <span style={{ fontSize: 11.5, color: "var(--danger)", lineHeight: 1.5 }}>⚠ {disconnectWarning(cal.name)}</span>
          <div style={{ display: "flex", gap: 8 }}>
            <button disabled={locked} onClick={onDisconnect}
              style={{ ...btn, background: "var(--danger)", color: "var(--on-accent)", border: "none", opacity: locked ? 0.5 : 1 }}>
              {busy === "delete" ? "Disconnecting…" : "Yes, disconnect"}
            </button>
            <button disabled={locked} onClick={() => setConfirming(false)} style={btn}>Keep it</button>
          </div>
        </div>
      )}

      {editing && (
        <EditPanel cal={cal} busy={busy === "update"}
          onSave={(body) => { onPatch(body); setEditing(false); }}
          onCancel={() => setEditing(false)} />
      )}
    </div>
  );
}

function AddCalendarForm({ busy, onAdd, onCancel }: {
  busy: boolean;
  onAdd: (body: { name: string; ics_url: string; auto_join: boolean; bot_name?: string }) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [icsUrl, setIcsUrl] = useState("");
  const [botName, setBotName] = useState("");
  const [autoJoin, setAutoJoin] = useState(true);
  const ready = !!name.trim() && !!icsUrl.trim();

  return (
    <div style={{ ...row, borderStyle: "dashed" }}>
      <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--t1)" }}>Add a calendar</div>
      <div style={meta}>
        Google Calendar: ⚙ Settings → your calendar → <b style={{ color: "var(--t2)" }}>Integrate calendar</b> → copy
        the <b style={{ color: "var(--t2)" }}>Secret address in iCal format</b>. Outlook: Settings → Calendar →
        Shared calendars → Publish a calendar. Not the public page or embed link — the secret address is a password.
      </div>
      <label style={labelled}>
        <span style={labelCol}>Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} placeholder="Work"
          style={field} aria-label="Calendar name" />
      </label>
      <label style={labelled}>
        <span style={labelCol}>Secret ICS</span>
        <input value={icsUrl} onChange={(e) => setIcsUrl(e.target.value)} type="password" autoComplete="off"
          placeholder="https://calendar.google.com/…/basic.ics" style={field} aria-label="Secret ICS address"
          onKeyDown={(e) => { if (e.key === "Enter" && ready && !busy) onAdd({ name: name.trim(), ics_url: icsUrl.trim(), auto_join: autoJoin, bot_name: botName.trim() || undefined }); }} />
      </label>
      <label style={labelled}>
        <span style={labelCol}>Bot name</span>
        <input value={botName} onChange={(e) => setBotName(e.target.value)} maxLength={100} placeholder="Vexa"
          style={field} aria-label="Bot name for the new calendar" />
      </label>
      <label style={{ ...checkRow, paddingLeft: 104 }}>
        <input type="checkbox" checked={autoJoin} onChange={(e) => setAutoJoin(e.target.checked)} />
        Auto-join meetings imported from this calendar
      </label>
      <div style={{ display: "flex", gap: 8, paddingLeft: 104 }}>
        <button disabled={busy || !ready}
          onClick={() => onAdd({ name: name.trim(), ics_url: icsUrl.trim(), auto_join: autoJoin, bot_name: botName.trim() || undefined })}
          style={{ ...primaryBtn, opacity: busy || !ready ? 0.5 : 1 }}>
          {busy ? "Connecting…" : "Connect"}
        </button>
        <button disabled={busy} onClick={onCancel} style={btn}>Cancel</button>
      </div>
    </div>
  );
}

export function CalendarConnectionsPanel() {
  const [cals, setCals] = useState<CalendarConnection[] | null>(null);
  const [stamps, setStamps] = useState<Record<string, CalendarSyncStamp>>({});
  const [busy, setBusy] = useState<string | null>(null);   // "new" | "<id>:sync|update|delete"
  const [adding, setAdding] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await listCalendars();
      setCals(list); setErr(null);
      // One bad stamp must not blank the page — each probe is caught on its own.
      const pairs = await Promise.all(list.map(async (c) => {
        try { return [c.id, await getCalendarSyncStatus(c.id)] as const; }
        catch { return [c.id, {} as CalendarSyncStamp] as const; }
      }));
      setStamps(Object.fromEntries(pairs));
    } catch (e: unknown) {
      setCals([]); setErr(presentError(e).headline);
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const atCap = (cals?.length ?? 0) >= MAX_CALENDARS;

  const add = async (body: { name: string; ics_url: string; auto_join: boolean; bot_name?: string }) => {
    setBusy("new"); setErr(null); setNote(null);
    try {
      const created = await createCalendar(body);
      // The backend does not sync on create — answer the user NOW instead of leaving a silent wait.
      let stamp: CalendarSyncStamp = {};
      try { stamp = await syncCalendar(created.id); }
      catch (e: unknown) { stamp = { last_error: presentError(e).headline }; }
      setStamps((s) => ({ ...s, [created.id]: stamp }));
      setAdding(false);
      setNote(stamp.last_error
        ? `“${created.name}” connected, but its first sync needs attention.`
        : `“${created.name}” connected — ${(stamp.counts?.created ?? 0) + (stamp.counts?.updated ?? 0)} upcoming meetings imported.`);
      refreshMeetings();
      await refresh();
    } catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(null); }
  };

  const patch = async (cal: CalendarConnection, body: CalendarUpdateBody) => {
    setBusy(`${cal.id}:update`); setErr(null); setNote(null);
    try {
      await updateCalendar(cal.id, body);
      if (patchNeedsSync(body)) {
        // PATCH does not reconcile existing planned meetings; the follow-up sync does.
        try { setStamps((s) => ({ ...s, [cal.id]: {} })); const st = await syncCalendar(cal.id); setStamps((s) => ({ ...s, [cal.id]: st })); }
        catch (e: unknown) { setStamps((s) => ({ ...s, [cal.id]: { last_error: presentError(e).headline } })); }
        refreshMeetings();
      }
      await refresh();
    } catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(null); }
  };

  const sync = async (cal: CalendarConnection) => {
    setBusy(`${cal.id}:sync`); setErr(null); setNote(null);
    try { const st = await syncCalendar(cal.id); setStamps((s) => ({ ...s, [cal.id]: st })); refreshMeetings(); }
    catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(null); }
  };

  const disconnect = async (cal: CalendarConnection) => {
    setBusy(`${cal.id}:delete`); setErr(null); setNote(null);
    try {
      await deleteCalendar(cal.id);
      setNote(`“${cal.name}” disconnected.`);
      refreshMeetings();
      await refresh();
    } catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(null); }
  };

  const busyFor = (id: string): string | null => (busy?.startsWith(`${id}:`) ? busy.slice(id.length + 1) : busy ? "other" : null);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 640 }}>
      <div style={{ ...meta, maxWidth: 460 }}>
        Connect a calendar&rsquo;s secret ICS feed and its scheduled meetings appear in Meetings by
        themselves; with auto-join on, the bot joins them when they start. Each calendar carries its own
        auto-join policy and bot name.
      </div>
      {err && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ {err}</div>}
      {note && <div role="status" style={{ fontSize: 11.5, color: "var(--green)" }}>✓ {note}</div>}

      {cals === null ? (
        <div style={meta}>Loading calendars…</div>
      ) : cals.length === 0 ? (
        <div style={meta}>No calendars connected yet.</div>
      ) : (
        <>
          <div style={{ fontSize: 11.5, color: "var(--t3)" }}>
            {cals.length} of {MAX_CALENDARS} calendars connected
          </div>
          {cals.map((c) => (
            <CalendarRow key={c.id} cal={c} stamp={stamps[c.id]} busy={busyFor(c.id)}
              onPatch={(b) => void patch(c, b)} onSync={() => void sync(c)} onDisconnect={() => void disconnect(c)} />
          ))}
        </>
      )}

      {adding ? (
        <AddCalendarForm busy={busy === "new"} onAdd={(b) => void add(b)} onCancel={() => setAdding(false)} />
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button disabled={atCap || busy !== null} onClick={() => { setAdding(true); setNote(null); }}
            style={{ ...btn, opacity: atCap || busy !== null ? 0.5 : 1 }}>+ Add calendar</button>
          {atCap && (
            <span style={meta}>
              You have all {MAX_CALENDARS} calendars connected — disconnect one to add another.
            </span>
          )}
        </div>
      )}
    </div>
  );
}
