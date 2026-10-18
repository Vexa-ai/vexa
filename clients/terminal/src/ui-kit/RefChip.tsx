"use client";
/** RefChip — a universal reference (`[[kind:id|label]]` or a `/r/<kind>/<id>` link) as a chip (#1783).
 *
 *  The server answers for THIS reader (`universalRefs.resolveUniversalRef`):
 *    readable     the object's own label; a click opens it where it lives
 *    not-yours    "Restricted" — nothing about the object, not even the label the writer typed — and
 *                 "Request access", which tells its owner
 *    unavailable  "Unavailable" (the access decision cannot tell not-yours from gone)
 *    gone         "Not found"
 *    grant        a join link: what it lets the holder into; a click opens the join page */
import { openSuppliedCard } from "./shellProvided";
import { useEffect, useState } from "react";
import { Icon } from "./index";
import type { RefAnswer } from "../data/workspaceApi";
import { parseUniversalRef, resolveUniversalRef } from "./universalRefs";
import { OPEN_ENTITY_EVENT, OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../platform";

const ICON: Record<string, string> = {
  workspace: "folder", chat: "msg", room: "msg", message: "msg", page: "file", file: "file", entity: "tag", meeting: "cal",
  flow: "zap", routine: "zap", sensor: "radio", join: "link", card: "tag", person: "user", query: "search", series: "cal",
};

export function openRef(a: RefAnswer): void {
  const o = (a.open ?? {}) as Record<string, string | number | undefined>;
  // the anchor says WHERE inside (a meeting moment, a transcript line, a heading, a message)
  const anchor = o.anchor ? String(o.anchor) : undefined;
  if (o.chat) { window.dispatchEvent(new CustomEvent(OPEN_SESSION_EVENT, { detail: { session: String(o.chat), ...(o.message ? { message: String(o.message) } : {}) } })); return; }
  if (o.card) { openSuppliedCard(String(o.card)); return; }
  if (o.path) { window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, { detail: { path: String(o.path), slug: o.slug ? String(o.slug) : undefined, ...(anchor ? { anchor } : {}) } })); return; }
  if (o.join) { window.location.assign(`/join/${o.join}`); return; }
  // a meeting opens on the right; the chat in the centre stays (founder rule 2026-10-16)
  if (o.meeting !== undefined) {
    const t = anchor?.startsWith("t=") ? anchor.slice(2) : undefined;
    const line = anchor && /^L\d+$/.test(anchor) ? anchor : undefined;
    window.dispatchEvent(new CustomEvent(OPEN_MEETING_EVENT, { detail: { ref: String(o.meeting), ...(t ? { at: t } : {}), ...(line ? { line } : {}) } }));
    return;
  }
  window.location.assign(a.url || "/");
}

export function RefChip({ text }: { text: string }) {
  const parsed = parseUniversalRef(text);
  const [a, setA] = useState<RefAnswer | undefined>(undefined);
  const [asked, setAsked] = useState<"no" | "sending" | "yes">("no");
  useEffect(() => {
    let on = true;
    void resolveUniversalRef(text).then((r) => { if (on) setA(r); });
    return () => { on = false; };
  }, [text]);
  const icon = <span className="vx-chip-icon" aria-hidden><Icon name={ICON[parsed?.kind ?? ""] ?? "link"} size={12} /></span>;

  if (a === undefined) {
    return <span className="vx-chip vx-entity vx-inline-chip" data-missing="">{icon}<span className="vx-chip-label">…</span></span>;
  }
  if (a.access === "readable" || a.access === "grant") {
    const label = a.label || "Untitled";
    return (
      <span role="link" tabIndex={0} className="vx-chip vx-entity vx-inline-chip" data-kind={a.kind}
        title={`Open ${label}`} onClick={() => openRef(a)} onKeyDown={(e) => { if (e.key === "Enter") openRef(a); }}>
        {icon}<span className="vx-chip-label">{label}</span>
      </span>
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
      <span className="vx-chip vx-entity vx-inline-chip" data-missing="" data-static="" aria-disabled="true">
        {icon}<span className="vx-chip-label">Restricted</span>
        {a.request_access && (asked === "yes"
          ? <span className="vx-ref-asked">Access requested</span>
          : <button type="button" className="vx-ref-ask" disabled={asked === "sending"} onClick={() => void ask()}>Request access</button>)}
      </span>
    );
  }
  return (
    <span className="vx-chip vx-entity vx-inline-chip" data-missing="" data-static="" aria-disabled="true">
      {icon}<span className="vx-chip-label">{a.access === "unavailable" ? "Unavailable" : "Not found"}</span>
    </span>
  );
}
