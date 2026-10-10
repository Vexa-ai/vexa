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
import { useEffect, useState } from "react";
import { Icon } from "./index";
import { useOpenEntity, ENTITY_CHIP, DEFAULT_ENTITY_CHIP } from "./docRefs";
import { humanize, parseWsRef, resolveLink, type ResolvedLink } from "./wsLinks";


/** The entity kind implied by a resolved path, for the chip's icon + colour. Read off the path the
 *  server returned rather than guessed from the ref: the ref carries an id, not a kind. */
function kindOf(r?: ResolvedLink | null): string | undefined {
  return r?.path ? /kg\/entities\/([^/]+)\//.exec(r.path)?.[1] : undefined;
}

export function WsLink({ refText, slug }: { refText: string; slug?: string }) {
  const parsed = parseWsRef(refText);
  // undefined = resolving. The FIRST paint shows the humanized target rather than the raw id: a
  // reader must never be shown `k4m5x2q7bd/nora-quill` while a request is in flight.
  const [target, setTarget] = useState<ResolvedLink | undefined>(undefined);
  const openEntity = useOpenEntity();

  useEffect(() => {
    let on = true;
    void resolveLink(refText, slug).then((r) => { if (on) setTarget(r); });
    return () => { on = false; };
  }, [refText, slug]);

  const title = target?.title || humanize(parsed?.target ?? refText);

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
    return (
      <span title={`In ${target.workspace ? `“${target.workspace}”` : "a workspace"} you don't have`}
        aria-disabled="true" className="vx-chip vx-entity vx-inline-chip" data-missing="" data-static="">
        <span className="vx-chip-icon" aria-hidden><Icon name="folder" size={12} /></span><span className="vx-chip-label">{title}</span>
      </span>
    );
  }

  const c = ENTITY_CHIP[kindOf(target) ?? ""] ?? DEFAULT_ENTITY_CHIP;
  const open = () => openEntity({ path: target.path ?? parsed?.target ?? "", slug: target.slug });
  return (
    <span role="link" tabIndex={0} className="vx-chip vx-entity vx-inline-chip" data-kind={kindOf(target) ?? "doc"}
      title={target.workspace ? `Open ${title} — in ${target.workspace}` : `Open ${title}`}
      onClick={open} onKeyDown={(e) => { if (e.key === "Enter") open(); }}>
      <span className="vx-chip-icon" aria-hidden style={{ color: c.color }}><Icon name={c.icon} size={12} /></span>
      <span className="vx-chip-label">{title}</span>
    </span>
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
