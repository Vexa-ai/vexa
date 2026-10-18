"use client";
/** MAKING A WORKSPACE (#1783, R3 3.5, `home2`; mock flow 12: `12a-ws-create`, `12b-ws-identity`,
 *  `12c-ws-invite`).
 *
 *    1 · New workspace: a name, what it is for, an optional site (for its logo), and who can see it —
 *        Private (only you), Shared (people you invite, from any org), Open (anyone can find and read
 *        it; only while `open_workspaces` is on).
 *    2 · It is made through the existing doors (`/api/workspace/new` private, `/api/workspace/shared/new`
 *        shared), its README is seeded as a rich front page, and it appears on the home as a card.
 *    3 · Its identity is PROPOSED, never set: the site's logo when one is given (the server finds and
 *        stores it), otherwise the agent designs a mark — both with `propose: true`. The proposal is a
 *        "needs you" item on the new card; the owner accepts or declines it there.
 *    4 · Invite (shared): an invite link is minted and the invitation emails are DRAFTED in the
 *        person's chat for them to send — nothing goes out on its own. */
import { useState } from "react";
import { Dialog } from "../../../ui-kit/primitives/Dialog";
import { Button } from "../../../ui-kit/primitives/Button";
import { useFeature } from "../../../platform/features";
import { writeWorkspaceFile } from "../../../data/workspaceApi";
import { toast } from "../../../ui-kit/primitives/Toast";
import { loadDesktop } from "../desktopApi";
import { askAgent } from "./acts";
import { loadAttention } from "./attentionApi";
import { aimHome } from "./HomeTarget";
import { openWorkspaceView } from "./workspaceView";
import "./home2.css";

export type Visibility = "private" | "shared" | "open";
const SITE = /^(?:https?:\/\/)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?:\/.*)?$/i;
const EMAIL = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/;

/** The seeded front page: the name, what it is for, and the live status of the place. */
export function seedReadme(name: string, purpose: string): string {
  const what = purpose.trim() || "What this workspace is for, in a sentence or two.";
  return `# ${name.trim()}\n\n${what}\n\n{{status}}\n\n## People\n\n{{members}}\n\n## Coming up\n\n{{upcoming}}\n`;
}

/** The bare domain of what the person typed as a site, or null. */
export function siteOf(raw: string): string | null {
  const m = SITE.exec(raw.trim());
  return m ? m[1].toLowerCase() : null;
}

/** The addresses in what the person typed (comma, semicolon or space separated), de-duplicated. */
export function emailsOf(raw: string): string[] {
  return [...new Set(raw.split(/[\s,;]+/).map((x) => x.trim().toLowerCase()).filter((x) => EMAIL.test(x)))];
}

async function postJson(url: string, body: unknown, method = "POST"): Promise<{ ok: boolean; status: number; data: Record<string, unknown> }> {
  try {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data: Record<string, unknown> = {};
    try { data = (await r.json()) as Record<string, unknown>; } catch { data = {}; }
    return { ok: r.ok, status: r.status, data };
  } catch {
    return { ok: false, status: 0, data: {} };
  }
}

/** Make it; answers its slug (the id every later verb uses), or null with the reason toasted. */
export async function createWorkspace(name: string, visibility: Visibility): Promise<string | null> {
  const r = visibility === "private"
    ? await postJson("/api/workspace/new", { name })
    : await postJson("/api/workspace/shared/new", { name });
  const slug = String(r.data.workspace_id ?? r.data.slug ?? "");
  if (!r.ok || !slug) { toast("The workspace couldn't be made just now"); return null; }
  return slug;
}

/** Ask for its identity, as a proposal the owner answers on the card. */
export async function proposeIdentity(slug: string, name: string, site: string | null): Promise<void> {
  if (site) {
    const r = await postJson(`/api/workspaces/${encodeURIComponent(slug)}/identity/icon`, { site, propose: true }, "PUT");
    if (r.ok) return;
  }
  // no site, or its logo could not be found: the agent designs a mark and proposes it
  askAgent(`Design a simple mark for my new workspace "${name}": one glyph or motif on a colour that suits `
    + `what it is for. Set it with workspace_icon_set (slug ${slug}) with propose: true, so I accept it on its `
    + `card. Then say one line.`, { hidden: true, display: `Give ${name} its image` });
}

/** Mint one invite link and ask the agent, IN the chat (a real turn, so its draft shows there), to
 *  draft invitation emails for people with no account here. Nothing is sent. */
export async function draftInvites(slug: string, name: string, emails: string[]): Promise<boolean> {
  if (!emails.length) return false;
  const link = await postJson("/api/workspace/invite-link", { slug, role: "contributor", max_uses: Math.max(emails.length, 1), expires_in_days: 14 });
  const url = typeof link.data.url === "string" ? link.data.url : "";
  askAgent(`Draft an invitation email (a Gmail draft — do not send it) to each of ${emails.join(", ")} `
    + `inviting them to the workspace "${name}"${url ? ` with this join link: ${url}` : ""}. Keep it short, `
    + `in my voice, show me the draft here, and tell me in one line where the drafts are.`,
    { display: `Draft an invite to ${name} for ${emails.join(", ")}` });
  return true;
}

export type InviteOutcome = { added: string[]; already: string[]; outside: string[]; failed: string[] };

/** INVITE (JTBD-1): somebody who already has an account is ADDED, through the workspace membership
 *  path (they get an in-app notice and the workspace shows on their home). Only an address with no
 *  account gets an email invite, drafted by the agent in the chat. */
export async function inviteMembers(slug: string, name: string, emails: string[]): Promise<InviteOutcome> {
  const out: InviteOutcome = { added: [], already: [], outside: [], failed: [] };
  for (const email of emails) {
    const r = await postJson("/api/workspace/members/add", { workspace: slug, person: email, role: "write" });
    if (r.ok) (r.data.already_member ? out.already : out.added).push(email);
    else if (r.status === 404) out.outside.push(email);
    else out.failed.push(email);
  }
  if (out.outside.length) await draftInvites(slug, name, out.outside);
  return out;
}

function inviteSentence(r: InviteOutcome, name: string): string {
  const parts: string[] = [];
  if (r.added.length) parts.push(`${r.added.join(", ")} ${r.added.length === 1 ? "is" : "are"} now in ${name}`);
  if (r.already.length) parts.push(`${r.already.join(", ")} ${r.already.length === 1 ? "was" : "were"} already in it`);
  if (r.outside.length) parts.push(`an email invite for ${r.outside.join(", ")} is being drafted in your chat, nothing is sent`);
  if (r.failed.length) parts.push(`${r.failed.join(", ")} could not be added just now`);
  return parts.join("; ");
}

export function NewWorkspaceDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const openOn = useFeature("open_workspaces");
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [site, setSite] = useState("");
  const [vis, setVis] = useState<Visibility>("shared");
  const [busy, setBusy] = useState(false);
  const [made, setMade] = useState<{ slug: string; name: string } | null>(null);
  const reset = () => { setName(""); setPurpose(""); setSite(""); setVis("shared"); setMade(null); setBusy(false); };
  const close = () => { reset(); onClose(); };
  const create = async () => {
    const n = name.trim();
    if (!n || busy) return;
    setBusy(true);
    if (vis === "open") return;
    const slug = await createWorkspace(n, vis);
    if (!slug) { setBusy(false); return; }
    try { await writeWorkspaceFile("README.md", seedReadme(n, purpose), { slug }); } catch { /* the template README stays */ }
    await proposeIdentity(slug, n, siteOf(site));
    await loadDesktop({ force: true });
    void loadAttention({ force: true });
    aimHome(slug);
    void openWorkspaceView({ slug, name: n });
    setBusy(false);
    if (vis === "private") { toast(`${n} is ready`); close(); return; }
    setMade({ slug, name: n });
  };
  if (made) return <InviteDialog open={open} slug={made.slug} name={made.name} onClose={close} />;
  return (
    <Dialog open={open} onClose={close} title="New workspace" footer={<>
      <Button onClick={close}>Cancel</Button>
      <Button variant="primary" disabled={!name.trim() || busy} onClick={() => void create()}>{busy ? "Making…" : "Create"}</Button></>}>
      <label className="vx-nw-field"><span>Name</span>
        <input autoFocus data-autofocus value={name} onChange={(e) => setName(e.target.value)} maxLength={80} aria-label="Name"
          onKeyDown={(e) => { if (e.key === "Enter") void create(); }} /></label>
      <label className="vx-nw-field"><span>What is it for? <small>optional</small></span>
        <input value={purpose} onChange={(e) => setPurpose(e.target.value)} maxLength={300} aria-label="What is it for" /></label>
      <label className="vx-nw-field"><span>Company or project site <small>optional — for its logo</small></span>
        <input value={site} onChange={(e) => setSite(e.target.value)} placeholder="example.com" aria-label="Site" /></label>
      <fieldset className="vx-nw-vis"><legend>Who can see it</legend>
        {([["private", "Private", "Only you"], ["shared", "Shared", "People you invite, from any org"],
          ...(openOn ? [["open", "Open", "Anyone can find and read it; people join to take part — making one opens with the directory, not yet here"]] : [])] as [Visibility, string, string][]).map(([v, label, sub]) => (
          <label key={v} className="vx-nw-opt" data-on={vis === v ? "" : undefined} data-disabled={v === "open" ? "" : undefined}>
            {/* OPEN has no creating door yet (the directory reads open workspaces; nothing makes one):
                shown so the choice is known, never silently made shared instead */}
            <input type="radio" name="vx-nw-vis" value={v} checked={vis === v} disabled={v === "open"} onChange={() => setVis(v)} />
            <span><b>{label}</b><small>{sub}</small></span></label>))}
      </fieldset>
    </Dialog>
  );
}

/** Invite people to a shared workspace: accounts are added in-app; other addresses get a drafted email. */
export function InviteDialog({ open, slug, name, onClose }: { open: boolean; slug: string; name: string; onClose: () => void }) {
  const [emails, setEmails] = useState("");
  const [busy, setBusy] = useState(false);
  const n = emailsOf(emails).length;
  const close = () => { setEmails(""); setBusy(false); onClose(); };
  const invite = async () => {
    const list = emailsOf(emails);
    if (!list.length) return;
    setBusy(true);
    toast(inviteSentence(await inviteMembers(slug, name, list), name));
    close();
  };
  return (
    <Dialog open={open} onClose={close} title={`Invite to ${name}`} footer={<>
      <Button onClick={close}>Not now</Button>
      <Button variant="primary" disabled={!n || busy} onClick={() => void invite()}>{n > 1 ? `Invite ${n}` : "Invite"}</Button></>}>
      <label className="vx-nw-field"><span>People</span>
        <input autoFocus data-autofocus value={emails} onChange={(e) => setEmails(e.target.value)} placeholder="name@company.com, …" aria-label="People to invite" /></label>
      <p className="vx-nw-note">People already on Vexa are added now and see it on their home. Anyone else gets an email invite, drafted in your chat for you to send.</p>
    </Dialog>
  );
}
