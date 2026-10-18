"use client";
/** WsLink — the chip a cross-workspace `[[ws:<id>/<target>]]` renders as.
 *
 *  Three states, and the design rule for all three is that NOTHING 404s inside a page
 *  (PRD decision 26.3):
 *
 *    readable   a normal entity chip: the target's title now, opening it in the panel;
 *    not-yours  greyed, dashed, NOT clickable — the title plus "in a workspace you don't have".
 *               Deliberately not an error and deliberately not an invitation: a click that
 *               asks for something the reader cannot have teaches nothing;
 *    gone       the last known title as plain text. No chip, no click, no explanation the reader
 *               did not ask for — a workspace that is no longer there is not a fault they can fix.
 *
 *  It is a SEPARATE component from `Wikilink` rather than a branch inside it, because the two
 *  resolve through different machinery — a tree search here, a server answer there — and one
 *  component with two async paths would have to hold both, plus rules-of-hooks around the branch.
 */
import { useEffect, useState, type ReactNode } from "react";
import { Icon } from "./index";
import { ContextMenu } from "./ContextMenu";
import { usePeekOrOpen } from "./peekSeam";
import { useOpenEntity, ENTITY_CHIP, DEFAULT_ENTITY_CHIP } from "./docRefs";
import { humanize, parseWsRef, resolveLink, type ResolvedLink } from "./wsLinks";


/** The entity kind implied by a resolved path, for the chip's icon + colour. Read off the path the
 *  server returned rather than guessed from the ref: the ref carries an id, not a kind. */
function kindOf(r?: ResolvedLink | null): string | undefined {
  return r?.path ? /kg\/entities\/([^/]+)\//.exec(r.path)?.[1] : undefined;
}

/** The words a qualified ref reads as before anything resolves it: `biz:graph/sg/Growth` → "Growth". */
function refWords(refText: string): string {
  const parsed = parseWsRef(refText);
  if (parsed) return parsed.target;
  const q = /^(?:vexa:\/\/ws\/[^/]+\/|[A-Za-z0-9][\w.-]{0,63}:(?!\/\/))(.+)$/.exec(refText.trim());
  return q ? q[1] : refText;
}

/** Links that name another workspace by its SLUG (#1783): `biz:graph/sg/Growth`, `vexa://ws/biz/…`.
 *  A prefix is a qualifier only when it is slug-shaped and not a URL scheme. */
const NOT_SLUGS = new Set(["http", "https", "mailto", "file", "data", "javascript", "ws", "vexa"]);
export function isQualifiedRef(inner: string): boolean {
  const t = (inner ?? "").trim();
  if (/^vexa:\/\/ws\/[A-Za-z0-9][\w.-]*\/.+/i.test(t)) return true;
  const m = /^([A-Za-z0-9][\w.-]{0,63}):(?!\/\/)(\S.*)$/.exec(t);
  return !!m && !NOT_SLUGS.has(m[1].toLowerCase()) && !/\s/.test(m[1]) && /[/.\-_]|^\S+$/.test(m[2]);
}

export function WsLink({ refText, slug, label }: { refText: string; slug?: string; label?: ReactNode }) {
  const parsed = parseWsRef(refText);
  const [pick, setPick] = useState<{ x: number; y: number } | null>(null);
  // undefined = resolving. The FIRST paint shows the humanized target rather than the raw id: a
  // reader must never be shown `k4m5x2q7bd/nora-quill` while a request is in flight.
  const [target, setTarget] = useState<ResolvedLink | undefined>(undefined);
  const openEntity = useOpenEntity();

  useEffect(() => {
    let on = true;
    void resolveLink(refText, slug).then((r) => { if (on) setTarget(r); });
    return () => { on = false; };
  }, [refText, slug]);

  const title = target?.title || humanize(refWords(refText));
  const peek = usePeekOrOpen(target?.access === "readable" && target.path ? { path: target.path, slug: target.slug, title: target.title, kind: kindOf(target) } : null);
  const shown = label ?? title;

  if (target === undefined) {
    return (
      <span className="vx-chip vx-entity vx-inline-chip" data-missing="">
        <span className="vx-chip-icon" aria-hidden><Icon name="link" size={12} /></span><span className="vx-chip-label">{title}</span>
      </span>
    );
  }

  if (target.access === "gone") {
    // PLAIN TEXT, no tooltip, no icon. The workspace is not there any more; saying so in a hover
    // the reader has to discover would be explaining our storage to somebody reading a sentence.
    return <span className="vx-ws-gone">{title}</span>;
  }

  if (target.access === "not-yours") {
    // RESTRICTED: the words of the ref, never the page's own title — this reader cannot read it.
    return (
      <span title={`Restricted — in ${target.workspace ? `“${target.workspace}”` : "a workspace"} you don't have`}
        aria-disabled="true" className="vx-chip vx-entity vx-inline-chip" data-missing="" data-static="" data-restricted="">
        <span className="vx-chip-icon" aria-hidden><Icon name="shield" size={12} /></span><span className="vx-chip-label">{shown}</span>
        <span className="vx-chip-meta">restricted</span>
      </span>
    );
  }

  // A PAGE THAT DOES NOT EXIST IS PLAIN TEXT (founder, live: "pages do not exist, so what's the
  // point?") — no chip, no picker; the agent can still make the page when asked.
  if (target.missing || (target.access === "readable" && !target.path && !target.ambiguous)) {
    return <span className="vx-ws-missing" data-ws-missing="">{shown}</span>;
  }

  // ONE PAGE IN THIS WORKSPACE WINS over a picker; a picker only when it is genuinely ambiguous,
  // each row the page's title and its workspace's name (never an address, never a count badge).
  const here = target.ambiguous ? target.candidates?.find((c) => c.slug && c.slug === slug) : undefined;
  if (here) {
    const open = () => openEntity({ path: here.path ?? "", slug: here.slug });
    return <span role="link" tabIndex={0} className="vx-chip vx-entity vx-inline-chip" data-kind="doc" onClick={open}
      onKeyDown={(e) => { if (e.key === "Enter") open(); }}>
      <span className="vx-chip-icon" aria-hidden><Icon name="link" size={12} /></span><span className="vx-chip-label">{shown}</span></span>;
  }
  if (target.ambiguous && target.candidates?.length) {
    // MORE THAN ONE PAGE BY THIS NAME, in workspaces the reader can read: a small picker.
    const cands = target.candidates.filter((c) => !String(c.workspace ?? "").includes("@"));
    return (
      <>
        <span role="button" tabIndex={0} aria-haspopup="menu" aria-expanded={!!pick} className="vx-chip vx-entity vx-inline-chip" data-ambiguous=""
          title={`${cands.length} pages by this name — choose one`}
          onClick={(e) => { const b = (e.currentTarget as HTMLElement).getBoundingClientRect(); setPick(pick ? null : { x: b.left, y: b.bottom + 4 }); }}
          onKeyDown={(e) => { if (e.key === "Enter") { const b = (e.currentTarget as HTMLElement).getBoundingClientRect(); setPick({ x: b.left, y: b.bottom + 4 }); } }}>
          <span className="vx-chip-icon" aria-hidden><Icon name="link" size={12} /></span>
          <span className="vx-chip-label">{shown}</span>
        </span>
        {pick && <ContextMenu x={pick.x} y={pick.y} label={`Pages named ${title}`} onClose={() => setPick(null)}
          items={cands.map((c, i) => ({ id: `c${i}`, label: c.title, detail: c.workspace ?? undefined,
            onSelect: () => { setPick(null); openEntity({ path: c.path ?? "", slug: c.slug }); } }))} />}
      </>
    );
  }

  const c = ENTITY_CHIP[kindOf(target) ?? ""] ?? DEFAULT_ENTITY_CHIP;
  const open = () => (target.path ? peek.onActivate() : openEntity({ path: parsed?.target ?? "", slug: target.slug }));
  return (
    <>
    <span role="link" tabIndex={0} className="vx-chip vx-entity vx-inline-chip" data-kind={kindOf(target) ?? "doc"} {...peek.bind}
      aria-label={target.workspace ? `Open ${title} — in ${target.workspace}` : `Open ${title}`}
      onClick={open} onKeyDown={(e) => { if (e.key === "Enter") open(); }}>
      <span className="vx-chip-icon" aria-hidden style={{ color: c.color }}><Icon name={c.icon} size={12} /></span>
      <span className="vx-chip-label">{shown}</span>
    </span>
    {peek.peek}
    </>
  );
}

/** WorkspaceName — a mounted workspace's NAME where the client used to print its slug.
 *
 *  F49: the chat header read `126`. That is the directory a desk happens to live in, and it was
 *  the one word the header was about. */
export function WorkspaceName({ slug, fallback }: { slug: string; fallback?: string }) {
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    let on = true;
    void import("./wsLinks").then((m) => m.workspaceBySlug(slug))
      .then((rec) => { if (on && rec?.name) setName(rec.name); })
      .catch(() => { /* the slug stays — a worse label than a name, a better one than nothing */ });
    return () => { on = false; };
  }, [slug]);
  return <>{name ?? fallback ?? slug}</>;
}
