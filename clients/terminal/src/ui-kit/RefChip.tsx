"use client";
/** RefChip — a universal reference (`[[kind:id|label]]` or a `/r/<kind>/<id>` link) as a BADGE (#1783).
 *
 *  Founder, 2026-10-18: *"we need full reference for everything when we reference, rendered as badge
 *  (not string uuid)"*. A badge is an icon, the object's name, and a short DISAMBIGUATOR — a meeting's
 *  date and time (and owner), a page's workspace, a person's role, a commit's short hash — because
 *  many objects share a name ("Call with Vexa"). The id itself is never drawn.
 *
 *  The server answers for THIS reader (`universalRefs.resolveUniversalRef`):
 *    readable     the object's own label and disambiguator; a click opens it where it lives
 *    not-yours    "Restricted" — nothing about the object, not even the label the writer typed — and
 *                 "Request access", which tells its owner
 *    unavailable  a muted "Unavailable" — also for a reference that resolves to nothing: the reader
 *    gone         never sees raw `[[…]]` syntax or an id
 *    grant        a join link: what it lets the holder into; a click opens the join page
 *
 *  COPY KEEPS THE REFERENCE: a badge carries its reference in `data-ref`, and copying a selection that
 *  holds badges puts the references on the clipboard (`installRefCopy`), so a paste into the composer
 *  is the same badge again, not its name. */
import { openSuppliedCard } from "./shellProvided";
import { useEffect, useState } from "react";
import { Icon } from "./index";
import { Modal } from "./Modal";
import type { RefAnswer } from "../data/workspaceApi";
import { parseUniversalRef, refChip, refHint, resolveUniversalRef } from "./universalRefs";
import { OPEN_ENTITY_EVENT, OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../platform";
import { navigateWithin } from "../platform/navigate";

const ICON: Record<string, string> = {
  workspace: "folder", chat: "msg", room: "msg", message: "msg", page: "file", file: "file", entity: "tag", meeting: "cal",
  flow: "zap", routine: "zap", sensor: "radio", join: "link", card: "tag", person: "user", query: "search", series: "cal",
  commit: "git", policy: "file",
};

/** A person badge asks the shell to open the person's profile. */
export const OPEN_PERSON_EVENT = "vexa:terminal:open-person";

/** Asked before the badge opens its own review: a shell that has the full commit review takes it. */
export const OPEN_COMMIT_EVENT = "vexa:terminal:open-commit";

export function openRef(a: RefAnswer, onCommit?: (sha: string, slug?: string) => void): void {
  const o = (a.open ?? {}) as Record<string, string | number | undefined>;
  // the anchor says WHERE inside (a meeting moment, a transcript line, a heading, a message)
  const anchor = o.anchor ? String(o.anchor) : undefined;
  if (o.commit) {
    const detail = { sha: String(o.commit), slug: o.slug ? String(o.slug) : undefined };
    const ev = new CustomEvent(OPEN_COMMIT_EVENT, { detail, cancelable: true });
    if (window.dispatchEvent(ev) && onCommit) onCommit(detail.sha, detail.slug);
    return;
  }
  // a person opens their profile on the right (the shell listens; UI 7mm)
  if (o.person) { window.dispatchEvent(new CustomEvent(OPEN_PERSON_EVENT, { detail: { id: String(o.person), name: a.label ?? "" } })); return; }
  if (o.chat) { window.dispatchEvent(new CustomEvent(OPEN_SESSION_EVENT, { detail: { session: String(o.chat), ...(o.message ? { message: String(o.message) } : {}) } })); return; }
  if (o.card) { openSuppliedCard(String(o.card)); return; }
  if (o.path) { window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, { detail: { path: String(o.path), slug: o.slug ? String(o.slug) : undefined, ...(anchor ? { anchor } : {}) } })); return; }
  if (o.join) { navigateWithin(`/join/${encodeURIComponent(String(o.join))}`); return; }
  // a meeting opens on the right; the chat in the centre stays (founder rule 2026-10-16)
  if (o.meeting !== undefined) {
    const t = anchor?.startsWith("t=") ? anchor.slice(2) : undefined;
    const line = anchor && /^L\d+$/.test(anchor) ? anchor : undefined;
    window.dispatchEvent(new CustomEvent(OPEN_MEETING_EVENT, { detail: { ref: String(o.meeting), ...(t ? { at: t } : {}), ...(line ? { line } : {}) } }));
    return;
  }
  navigateWithin(a.url);
}

// ── copy keeps the reference ───────────────────────────────────────────────────────────────────────
let copyInstalled = false;
/** Text of a copied fragment with every badge replaced by its reference. */
export function textWithRefs(root: ParentNode): string {
  const box = document.createElement("div");
  box.appendChild(root.cloneNode(true));
  box.querySelectorAll<HTMLElement>("[data-ref]").forEach((el) => el.replaceWith(document.createTextNode(el.dataset.ref ?? "")));
  return box.textContent ?? "";
}
export function installRefCopy(): void {
  if (copyInstalled || typeof document === "undefined") return;
  copyInstalled = true;
  document.addEventListener("copy", (e: ClipboardEvent) => {
    const sel = document.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed || !e.clipboardData) return;
    const range = sel.getRangeAt(0);
    const start = range.commonAncestorContainer;
    const inside = (start instanceof Element ? start : start.parentElement)?.closest<HTMLElement>("[data-ref]");
    let text: string;
    if (inside) text = inside.dataset.ref ?? "";
    else {
      const frag = range.cloneContents();
      if (!frag.querySelector("[data-ref]")) return;
      text = textWithRefs(frag);
    }
    e.clipboardData.setData("text/plain", text);
    e.preventDefault();
  });
}

function CommitReview({ sha, slug, label, onClose }: { sha: string; slug?: string; label: string; onClose: () => void }) {
  const [diff, setDiff] = useState<{ text: string; truncated?: boolean } | null | "error">(null);
  useEffect(() => {
    let on = true;
    void import("../data/workspaceApi")
      .then((api) => api.readWorkspaceGitDiff({ sha, slug: slug && slug !== "personal" ? slug : undefined }))
      .then((d) => { if (on) setDiff({ text: d.diff, truncated: d.truncated }); })
      .catch(() => { if (on) setDiff("error"); });
    return () => { on = false; };
  }, [sha, slug]);
  return (
    <Modal title={label} onClose={onClose} width={720}>
      <div data-commit-review={sha} className="t-xs c-3 vx-commit-sha"><Icon name="git" size={12} /> {sha.slice(0, 7)}</div>
      {diff === null ? <div className="t-sm c-3">Loading the change…</div>
        : diff === "error" ? <div className="t-sm c-3">Couldn't read this change.</div>
          : <pre className="vx-code vx-commit-diff">
            {(diff.text || "No file changes.").split("\n").map((l, i) => (
              <div key={i} data-line={l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : undefined}>{l || " "}</div>))}
            {diff.truncated && <div className="c-3">…</div>}
          </pre>}
    </Modal>
  );
}

export function RefChip({ text }: { text: string }) {
  const parsed = parseUniversalRef(text);
  const [a, setA] = useState<RefAnswer | undefined>(undefined);
  const [asked, setAsked] = useState<"no" | "sending" | "yes">("no");
  const [commit, setCommit] = useState<{ sha: string; slug?: string } | null>(null);
  useEffect(() => { installRefCopy(); }, []);
  useEffect(() => {
    let on = true;
    void resolveUniversalRef(text).then((r) => { if (on) setA(r); });
    return () => { on = false; };
  }, [text]);
  const kind = parsed?.kind ?? "";
  // the reference itself, without the writer's label — what a copy carries
  const ref = parsed ? refChip(parsed.kind, parsed.id, undefined, parsed.anchor) : text;
  const icon = <span className="vx-chip-icon" aria-hidden><Icon name={ICON[kind] ?? "link"} size={12} /></span>;

  if (a === undefined) {
    return <span className="vx-chip vx-entity vx-inline-chip" data-missing="" data-kind={kind} data-ref={ref}>{icon}<span className="vx-chip-label">…</span></span>;
  }
  if (a.access === "readable" || a.access === "grant") {
    const label = a.label || `Untitled ${kind === "flow" ? "routine" : kind || "item"}`;
    const hint = refHint(a.kind || kind, a.hint);
    const open = () => openRef(a, (sha, slug) => setCommit({ sha, slug }));
    return (
      <>
        <span role="link" tabIndex={0} className="vx-chip vx-entity vx-inline-chip" data-kind={a.kind} data-ref={ref} draggable
          onDragStart={(e) => { e.dataTransfer?.setData("text/plain", ref); e.dataTransfer?.setData("application/x-vexa-ref", ref); }}
          title={hint ? `Open ${label} · ${hint}` : `Open ${label}`} onClick={open} onKeyDown={(e) => { if (e.key === "Enter") open(); }}>
          {icon}<span className="vx-chip-label">{label}</span>
          {hint && <span className="vx-chip-hint" data-ref-hint="">· {hint}</span>}
        </span>
        {commit && <CommitReview sha={commit.sha} slug={commit.slug} label={label} onClose={() => setCommit(null)} />}
      </>
    );
  }
  if (a.access === "not-yours") {
    const ask = async () => {
      setAsked("sending");
      try {
        const api = await import("../data/workspaceApi");
        await api.requestRefAccess(text);
        setAsked("yes");
      } catch { setAsked("no"); }
    };
    return (
      <span className="vx-chip vx-entity vx-inline-chip" data-missing="" data-static="" aria-disabled="true" data-kind={kind} data-ref={ref}>
        {icon}<span className="vx-chip-label">Restricted</span>
        {a.request_access && (asked === "yes"
          ? <span className="vx-ref-asked">Access requested</span>
          : <button type="button" className="vx-ref-ask" disabled={asked === "sending"} onClick={() => void ask()}>Request access</button>)}
      </span>
    );
  }
  // unavailable or gone: one calm, muted word — never the raw reference and never its id
  return (
    <span className="vx-chip vx-entity vx-inline-chip" data-missing="" data-static="" aria-disabled="true" data-kind={kind} data-ref={ref}
      data-access={a.access} title={a.access === "gone" ? "This no longer exists, or never did" : "You can't open this"}>
      {icon}<span className="vx-chip-label">Unavailable</span>
    </span>
  );
}
