"use client";
/** The README lab — see README.md. Two viewers' render trees (from the server's renderer), the composer
 *  paste path, Share, pins and the message sheet, on stubbed routes. Never served in production. */
import { useEffect, useState, type ClipboardEvent } from "react";
import { Toaster } from "../../../ui-kit/primitives/Toast";
import { RefChip } from "../../../ui-kit/RefChip";
import { splitRefs } from "../../../ui-kit/universalRefs";
import { ReadmeDoc, type PageTree } from "../../../minutes/readme/ReadmeView";
import { ShareDialog } from "../../../minutes/readme/ShareDialog";
import { PinnedStrip } from "../../../minutes/readme/PinnedStrip";
import { MessageSheet, ObjectMenu, useLongPress } from "../../../minutes/readme/ObjectActions";
import { copyReference, meetingLinkIn, pasteToChip, referenceFromPaste, sendVexa } from "../../../surfaces/reference";
import trees from "./trees.json";
import { WorkspacePage } from "../../../minutes/desktop/home2/WorkspacePage";
import { loadDesktop } from "../../../minutes/desktop/desktopApi";
import { setFeatureOverride } from "../../../platform/features";
import "./lab.css";

type Call = { path: string; body?: unknown };
declare global { interface Window { __readmeLab?: { calls: Call[] } } }
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const REFS: Record<string, unknown> = {
  "[[meeting:153@00:12:30]]": { kind: "meeting", id: "153", url: "/r/meeting/153#t=00:12:30", access: "readable", label: "Venue call · 14 Oct", open: { meeting: 153, anchor: "t=00:12:30" }, request_access: false },
  // the lab's sharer is Dmitry, who owns this chat (Valeriia's README shows it Restricted from her tree)
  "[[chat:s-board]]": { kind: "chat", id: "s-board", url: "/r/chat/s-board", access: "readable", label: "Board prep merger 41", open: { chat: "s-board" }, request_access: false },
  "[[room:s-0]]": { kind: "room", id: "s-0", url: "/r/room/s-0", access: "readable", label: "general", open: { chat: "s-0" }, request_access: false },
};

const member = (subject: string, name: string, role: string) => ({ subject, name, initials: name.split(" ").map((w) => w[0]).join(""), role, presence: null });
const desktopFor = (viewer: "dmitry" | "valeriia") => ({
  version: 1, undo_version: null, proposals: [], filters: [], saved: [], hidden_workspaces: 0, presence_visibility: "workspace", cards_on: false,
  sections: [{ id: "workspaces", title: "Workspaces", hidden: false, collapsed: false, column: 0, span: 1, size: "m", items: [
    { kind: "workspace", ref: "workspace:tinkerers", slug: "tinkerers", id: "tinkerers", name: "AI Tinkerers Vienna", workspace_kind: "shared",
      write: viewer === "dmitry", access: viewer === "dmitry" ? "owner" : "reader",
      members: [member("u1", "Dmitry Grankin", "owner"), member("u2", "Valeriia Petrenko", "reader"), member("u3", "Marvin Hinz", "contributor"), member("u4", "Eugenia Sokolova", "contributor")] },
  ] }],
});

function install(viewer: "dmitry" | "valeriia" = "dmitry") {
  if (window.__readmeLab) return;
  const calls: Call[] = [];
  const real = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    let body: unknown;
    try { body = init?.body ? JSON.parse(String(init.body)) : undefined; } catch { body = undefined; }
    const hit = (p: string) => path === p || path.startsWith(`${p}?`);
    if (hit("/api/refs/resolve")) {
      calls.push({ path });
      const ref = decodeURIComponent(path.split("ref=")[1] ?? "");
      return json(200, REFS[ref] ?? { kind: "page", id: "x", url: "", access: "gone", label: null, open: null, request_access: false });
    }
    if (hit("/api/refs/audience")) {
      calls.push({ path, body });
      const ref = String((body as { ref?: string })?.ref ?? "");
      return json(200, ref.startsWith("[[chat:") ? { cannot: ["Valeriia"], cannot_count: 1, where: "your desk", everyone: false } : { cannot: [], cannot_count: 0, everyone: true });
    }
    if (hit("/api/sessions")) return json(200, { sessions: [{ session: "s-0", session_id: "s-0", title: "general", participants: 4, role: "owner" }, { session: "s-1", session_id: "s-1", title: "speakers", participants: 3, role: "member" }] });
    if (/^\/api\/sessions\/[^/]+\/messages$/.test(path)) { calls.push({ path, body }); return json(200, { message: { id: "n1" } }); }
    if (hit("/api/people/mentionable")) return json(200, { people: [{ subject: "u2", name: "Valeriia Petrenko" }, { subject: "u3", name: "Marvin Hinz" }] });
    if (hit("/api/pins")) {
      calls.push({ path, body });
      return json(200, { pins: [
        { ref: "[[page:abcdefghij/venue.md]]", by: "Dmitry", at: 1792249200, access: "readable", label: "Venue notes", open: { path: "venue.md" } },
        { ref: "[[chat:s-board]]", by: "Dmitry", at: 1792245600, access: "restricted" }] });
    }
    if (hit("/api/rooms/pair")) return json(404, { detail: "not here yet" });
    if (hit("/api/bots")) { calls.push({ path, body }); return json(201, { id: 1 }); }
    if (hit("/api/desktop/card/read")) return json(200, { card: { title: "Venue contract" } });
    if (hit("/api/desktop/page")) return json(200, viewer === "dmitry" ? trees.founder : trees.valeriia);
    if (hit("/api/desktop")) return json(200, desktopFor(viewer));
    if (hit("/api/desktop/attention")) return json(200, { generated_at: 1, needs: [], workspaces: {} });
    return real(input, init);
  };
  window.__readmeLab = { calls };
}

function Pane({ tree, who }: { tree: PageTree; who: string }) {
  const people = tree.nodes.find((n) => n.t === "block" && n.name === "people");
  const count = people && "rows" in people ? (people.rows?.length ?? 0) : 0;
  return <section className="lab-rd-pane" data-viewer={who}>
    <header className="lab-rd-head"><h1>{tree.title}</h1><span className="lab-rd-who">{count} people · as {who} ({tree.role})</span></header>
    <ReadmeDoc tree={tree} hideTitle />
  </section>;
}

export function ReadmeLab() {
  const [ready, setReady] = useState(false);
  const [view, setView] = useState<"both" | "dmitry" | "valeriia">("both");
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [value, setValue] = useState("");
  const [offer, setOffer] = useState<ReturnType<typeof meetingLinkIn>>(null);
  const [sharing, setSharing] = useState<null | { kind: string; id: string; anchor?: string; label?: string }>(null);
  const [sheet, setSheet] = useState(false);
  const press = useLongPress(() => setSheet(true));
  const [page, setPage] = useState<"" | "dmitry" | "valeriia">("");
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const pg = q.get("page");
    const pv = pg === "dmitry" || pg === "valeriia" ? pg : "";
    install(pv || "dmitry");
    if (pv) { setFeatureOverride({ home2: true, readme_page: true, desktop: true }); setPage(pv); void loadDesktop({ force: true }); }
    const t = q.get("theme"); if (t === "light" || t === "dark") setTheme(t);
    const v = q.get("view"); if (v === "dmitry" || v === "valeriia" || v === "both") setView(v);
    setReady(true);
  }, []);
  useEffect(() => {
    if (theme === "light") document.documentElement.setAttribute("data-theme", "light");
    else document.documentElement.setAttribute("data-theme", "dark");
  }, [theme]);
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const text = e.clipboardData.getData("text/plain");
    const chips = referenceFromPaste(e.clipboardData);
    if (chips !== null) { e.preventDefault(); setValue(pasteToChip(e.currentTarget, text, chips)); return; }
    setOffer(meetingLinkIn(text));
  };
  if (!ready) return null;
  // ?page=dmitry|valeriia — the real workspace page (fixed header + the README for that viewer)
  if (page) return <div className="lab-rd" data-lab-page={page}><WorkspacePage slug="tinkerers" /><Toaster /></div>;
  const moment = { kind: "meeting", id: "153", anchor: "t=00:12:30", label: "Venue call · 14 Oct" };
  return <div className="lab-rd">
    <div className="lab-rd-bar">
      {(["both", "dmitry", "valeriia"] as const).map((v) => <button key={v} type="button" className="vx-btn" data-variant="secondary" data-size="sm" aria-pressed={view === v} onClick={() => setView(v)}>{v}</button>)}
      <button type="button" className="vx-btn" data-variant="secondary" data-size="sm" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? "Light" : "Dark"}</button>
    </div>
    <div className="lab-rd-cols">
      {view !== "valeriia" && <Pane tree={trees.founder as unknown as PageTree} who="Dmitry" />}
      {view !== "dmitry" && <Pane tree={trees.valeriia as unknown as PageTree} who="Valeriia" />}
    </div>
    <section className="lab-rd-sec" data-lab="room">
      <h2>A room header: its Pinned strip</h2>
      <div className="lab-rd-pane"><b># general</b><PinnedStrip workspace="tinkerers" room="s-0" canPin /></div>
    </section>
    <section className="lab-rd-sec" data-lab="refs">
      <h2>References, paste, Share</h2>
      <div className="lab-rd-bar">
        <button type="button" className="vx-btn" data-variant="secondary" data-size="sm" data-lab-copy="" onClick={() => void copyReference(moment)}>Copy reference (meeting at 00:12:30)</button>
        <button type="button" className="vx-btn" data-variant="secondary" data-size="sm" data-lab-share="" onClick={() => setSharing(moment)}>Share the meeting moment…</button>
        <button type="button" className="vx-btn" data-variant="secondary" data-size="sm" data-lab-share-chat="" onClick={() => setSharing({ kind: "chat", id: "s-board", label: "Board prep" })}>Share a private chat…</button>
      </div>
      <textarea className="lab-rd-compose" aria-label="Composer" placeholder="Paste a Vexa link or a meeting link here" value={value}
        onChange={(e) => setValue(e.target.value)} onPaste={onPaste} />
      {offer && <div className="lab-rd-bar" data-send-vexa=""><button type="button" className="vx-btn" data-variant="secondary" data-size="sm" onClick={() => { const m = offer; setOffer(null); void sendVexa(m); }}>Send Vexa to this meeting</button></div>}
      <div data-lab-preview="">{splitRefs(value).map((p, i) => p.kind === "text" ? <span key={i}>{p.text}</span> : <RefChip key={i} text={p.text} />)}</div>
    </section>
    <section className="lab-rd-sec" data-lab="message">
      <h2>A room message (long-press on a phone, ⋯ on desktop)</h2>
      <div className="lab-rd-msg" {...press} data-lab-msg=""><span>Marvin: the venue is confirmed for 8 Nov</span>
        <ObjectMenu target={{ kind: "message", id: "s-0/12", label: "Marvin's message" }} workspace="tinkerers" room="s-0" /></div>
    </section>
    {sharing && <ShareDialog object={sharing} onClose={() => setSharing(null)} />}
    {sheet && <MessageSheet target={{ kind: "message", id: "s-0/12" }} onClose={() => setSheet(false)} />}
    <Toaster />
  </div>;
}
