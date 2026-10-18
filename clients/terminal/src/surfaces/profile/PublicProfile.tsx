"use client";
/** Public profiles (R4, feature `public_profiles`), from the approved mock (proto-on-product
 *  lab/desk/Flows.tsx flows 13–14):
 *   - `PublicProfileSettings` (13a): publish your own profile field by field; "Linked by N orgs",
 *     never which; Withdraw profile / Publish changes; verify a company domain.
 *   - `LinkedProfile` (13b/13c): on a CRM contact, the person's public fields LIVE next to your
 *     organisation's own notes; after a withdrawal, your organisation's data only. Link one from the
 *     directory, or unlink.
 *   - `Directory` (14b, people): search published profiles (3 letters at least; a few answers). */
import { useEffect, useState } from "react";
import { useFeature } from "../../platform/features";
import {
  FIELD_LABEL, PROFILE_FIELDS, checkCompanyVerification, first, linkProfile, readLinkedProfile, readOwnProfile,
  saveOwnProfile, searchDirectory, searchOpenWorkspaces, show, startCompanyVerification, withdrawOwnProfile,
  type LinkedRead, type OpenWorkspace, type OwnField, type OwnProfile, type ProfileFieldName, type PublicProfile,
} from "./profileApi";
import "./profile.css";

// ── 13a: your own profile ──────────────────────────────────────────────────────────────────────
export function PublicProfileSettings() {
  const on = useFeature("public_profiles");
  const [p, setP] = useState<OwnProfile | null>(null);
  const [draft, setDraft] = useState<Record<string, OwnField>>({});
  const [msg, setMsg] = useState("");
  const [domain, setDomain] = useState("");
  const [txt, setTxt] = useState("");
  useEffect(() => {
    if (!on) return;
    void readOwnProfile().then((x) => { setP(x); setDraft(x.fields); }).catch((e: Error) => setMsg(e.message));
  }, [on]);
  if (!on) return null;
  if (!p) return <div className="vx-settings-note" data-public-profile="">{msg || "Loading…"}</div>;
  const set = (k: ProfileFieldName, change: Partial<OwnField>) => setDraft((d) => ({ ...d, [k]: { ...d[k], ...change } }));
  const run = (work: () => Promise<OwnProfile>, said: string) =>
    void work().then((x) => { setP(x); setDraft(x.fields); setMsg(said); }).catch((e: Error) => setMsg(e.message));
  const v = p.company_verification;
  return (
    <div className="vx-pp" data-public-profile="">
      <div className="vx-pp-top">
        <span className="vx-settings-note">What you choose to publish about yourself. Nothing is public until you publish it.</span>
        <span className="vx-pp-chip" data-linked-by={p.linked_by}>Linked by {p.linked_by} {p.linked_by === 1 ? "org" : "orgs"}</span>
      </div>
      {PROFILE_FIELDS.map((k) => {
        const f = draft[k] ?? { value: k === "links" ? [] : "", public: false };
        return (
          <div key={k} className="vx-pp-row" data-field={k}>
            <label className="vx-pp-field"><b>{FIELD_LABEL[k]}</b>
              {k === "bio"
                ? <textarea value={String(f.value ?? "")} maxLength={500} rows={2} onChange={(e) => set(k, { value: e.target.value })} />
                : <input value={k === "links" ? (f.value as string[]).join(" ") : String(f.value ?? "")}
                    placeholder={k === "links" ? "https://… (space-separated)" : k === "photo" ? "https://… an image" : ""}
                    onChange={(e) => set(k, { value: k === "links" ? e.target.value.split(/\s+/).filter(Boolean) : e.target.value })} />}
            </label>
            <label className="vx-pp-toggle"><input type="checkbox" checked={!!f.public} onChange={(e) => set(k, { public: e.target.checked })} />
              {f.public ? "Public" : "Private"}</label>
          </div>
        );
      })}
      <p className="vx-settings-note">{p.linked_by} {p.linked_by === 1 ? "organisation links" : "organisations link"} a contact to this profile. You can see how many, never which, and never their notes.</p>
      <div className="vx-pp-acts">
        {p.published && <button type="button" className="vx-pp-btn" onClick={() => run(withdrawOwnProfile, "Withdrawn — nobody sees your profile now.")}>Withdraw profile</button>}
        <button type="button" className="vx-pp-btn" data-primary="" onClick={() => run(() => saveOwnProfile(draft as Record<ProfileFieldName, OwnField>, true), "Published.")}>Publish changes</button>
      </div>
      <div className="vx-pp-verify">
        <b>Company domain</b>
        {v?.verified_at ? <span className="vx-pp-chip" data-verified="">✓ {v.domain} verified</span> : <>
          <input placeholder="example.org" value={domain || v?.domain || ""} onChange={(e) => setDomain(e.target.value)} />
          <button type="button" className="vx-pp-btn" onClick={() => void startCompanyVerification(domain || v?.domain || "")
            .then((r) => setTxt(`Add a TXT record on ${r.domain}: ${r.txt}`)).catch((e: Error) => setMsg(e.message))}>Get the TXT record</button>
          {(v?.domain || txt) && <button type="button" className="vx-pp-btn" onClick={() => void checkCompanyVerification()
            .then((r) => { setMsg(r.verified_at ? `${r.domain} is verified.` : "Not yet — the TXT record was not found."); void readOwnProfile().then(setP); })
            .catch((e: Error) => setMsg(e.message))}>Check</button>}
          {(txt || v?.txt) && <code className="vx-pp-txt">{txt || `TXT on ${v?.domain}: ${v?.txt}`}</code>}
        </>}
      </div>
      {msg && <p className="vx-settings-note" role="status">{msg}</p>}
    </div>
  );
}

// ── 14b: the directory (people) ────────────────────────────────────────────────────────────────
export function DirectorySearch({ onPick, pickLabel = "View", people = true }: {
  onPick?: (p: PublicProfile) => void; pickLabel?: string;
  /** false: workspaces only (the contact's Link search passes nothing else) */
  people?: boolean;
}) {
  const openOn = useFeature("open_workspaces");
  const peopleOn = useFeature("public_profiles") && people;
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<PublicProfile[]>([]);
  const [spaces, setSpaces] = useState<OpenWorkspace[]>([]);
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState<PublicProfile | null>(null);
  useEffect(() => {
    if (q.trim().length < 3) { setHits([]); setSpaces([]); return; }
    const t = window.setTimeout(() => {
      // the contact's Link search is people only; the directory is open workspaces and people (mock 14b)
      const ws = openOn && !onPick ? searchOpenWorkspaces(q).catch(() => [] as OpenWorkspace[]) : Promise.resolve([] as OpenWorkspace[]);
      const pp = peopleOn ? searchDirectory(q) : Promise.resolve([] as PublicProfile[]);
      void Promise.all([ws, pp]).then(([w, h]) => { setSpaces(w); setHits(h); setMsg(w.length || h.length ? "" : "Nothing published matches."); })
        .catch((e: Error) => setMsg(e.message));
    }, 300);
    return () => window.clearTimeout(t);
  }, [q, openOn, peopleOn, onPick]);
  return (
    <div className="vx-dir" data-directory="">
      <input className="vx-dir-q" aria-label="Search the directory"
        placeholder={openOn && !onPick ? "Search open workspaces and published profiles (3 letters at least)" : "Search published profiles (3 letters at least)"}
        value={q} onChange={(e) => setQ(e.target.value)} />
      {spaces.length > 0 && <div className="vx-dir-h">Open workspaces</div>}
      {spaces.map((w) => (
        <div key={w.slug} className="vx-dir-row" data-open-workspace={w.slug}>
          <span className="vx-pp-face" aria-hidden>{initials(w.name || w.slug)}</span>
          <span className="vx-dir-who"><b>{w.name || w.slug} <span className="vx-pp-chip">Open</span></b>
            <small>{[w.description, w.pages != null ? `${w.pages} pages` : "", w.members != null ? `${w.members} members` : ""].filter(Boolean).join(" · ")}</small></span>
          <a className="vx-pp-btn" href={`/w/${encodeURIComponent(w.slug)}`}>Open</a>
        </div>
      ))}
      {hits.length > 0 && <div className="vx-dir-h">People</div>}
      {hits.map((p) => (
        <div key={p.handle} className="vx-dir-row">
          <span className="vx-pp-face" aria-hidden>{initials(String(p.fields.name ?? "?"))}</span>
          <span className="vx-dir-who"><b>{show(p.fields.name)}</b><small>{[p.fields.role, p.fields.company].filter(Boolean).map((x) => show(x)).join(", ")}{p.company_verified ? ` · ✓ ${p.company_verified}` : ""} · published profile</small></span>
          <button type="button" className="vx-pp-btn" onClick={() => (onPick ? onPick(p) : setOpen(p))}>{pickLabel}</button>
        </div>
      ))}
      {msg && <p className="vx-settings-note">{msg}</p>}
      {open && <ProfileCard p={open} />}
    </div>
  );
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "?";
}

function ProfileCard({ p }: { p: PublicProfile }) {
  return (
    <section className="vx-pp-box" data-profile-card="">
      {PROFILE_FIELDS.filter((k) => p.fields[k]).map((k) => (
        <div key={k} className="vx-pp-line"><b>{FIELD_LABEL[k]}</b><small>{show(p.fields[k])}</small></div>))}
      {p.company_verified && <span className="vx-pp-chip" data-verified="">✓ {p.company_verified} verified</span>}
    </section>
  );
}

// ── 13b / 13c: a CRM contact linked to a public profile ────────────────────────────────────────
export function LinkedProfile({ recordId, ownNotes }: { recordId: string; ownNotes?: string | null }) {
  const on = useFeature("public_profiles");
  const [r, setR] = useState<LinkedRead | null>(null);
  const [linking, setLinking] = useState(false);
  const [msg, setMsg] = useState("");
  const load = () => void readLinkedProfile(recordId).then(setR).catch(() => setR(null));
  useEffect(() => { if (on && recordId) load(); }, [on, recordId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!on || !r) return null;
  const name = String(r.profile?.fields.name ?? "");
  return (
    <section className="crm-section" aria-label="Public profile" data-linked-profile={r.linked ? (r.withdrawn ? "withdrawn" : "live") : "none"}>
      {r.withdrawn && <div className="vx-pp-banner">The person withdrew their public profile — this contact shows your organisation's own data only.</div>}
      <div className="vx-pp-two">
        <section className="vx-pp-box">
          {r.linked && r.profile ? <>
            <h3>From {first(name)}'s public profile <span className="vx-pp-chip">live</span></h3>
            {PROFILE_FIELDS.filter((k) => r.profile?.fields[k]).map((k) => (
              <div key={k} className="vx-pp-line"><b>{FIELD_LABEL[k]}</b><small>{show(r.profile?.fields[k])}</small></div>))}
            {r.profile.company_verified && <span className="vx-pp-chip" data-verified="">✓ {r.profile.company_verified} verified</span>}
            <small className="vx-settings-note">Published by {first(name)} themselves. Linked, never copied.</small>
            <button type="button" className="vx-pp-btn" onClick={() => void linkProfile(recordId, r.profile?.handle ?? "", false).then(load).catch((e: Error) => setMsg(e.message))}>Unlink</button>
          </> : <>
            <h3>Public profile</h3>
            <p className="vx-settings-note">{r.withdrawn ? "Withdrawn." : "Not linked. If this person published a profile, link it: their public fields then show here, live."}</p>
            {linking
              ? <DirectorySearch pickLabel="Link" onPick={(p) => void linkProfile(recordId, p.handle).then(() => { setLinking(false); load(); }).catch((e: Error) => setMsg(e.message))} />
              : <button type="button" className="vx-pp-btn" onClick={() => setLinking(true)}>Link a public profile</button>}
          </>}
        </section>
        <section className="vx-pp-box">
          <h3>Private notes <span className="vx-pp-chip">only your organisation</span></h3>
          <p className="vx-pp-notes">{ownNotes || "No notes yet."}</p>
          <small className="vx-settings-note">Never shared, merged or matched across organisations.</small>
        </section>
      </div>
      {msg && <p className="crm-alert" role="alert">{msg}</p>}
    </section>
  );
}
