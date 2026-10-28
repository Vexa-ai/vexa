"use client";
/** HOVER TO SEE, CLICK TO GO (#1783; founder 2026-10-15 on an entity chip's big in-place card: "this
 *  takes too much space — make it small and only on hover, click should open page"). One seam for
 *  every entity chip and page link, in pages, chat replies, tables and the home: a click (or Enter)
 *  opens the page, in the right panel like any page; hovering or focusing it shows the small
 *  `hoverCard` — the name and at most three short lines read from the page (`hoverFacts`). */
import type { ReactNode } from "react";
import { readWorkspaceFile } from "../data/workspaceApi";
import { Icon } from "./index";
import { useOpenEntity, ENTITY_CHIP, DEFAULT_ENTITY_CHIP } from "./docRefs";
import { useHoverCard } from "./hoverCard";
import { Identity } from "./Identity";
import { hoverFacts, hoverStory, pageSummary } from "./hoverFacts";
import { primeHoverCache } from "./hoverCard";

/** Ask agent-api for the page's story once; when it comes, the card's cache holds it. */
async function backfillSummary(t: PeekTarget, key: string): Promise<void> {
  try {
    const r = await fetch("/api/entities/summary", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(t.slug ? { path: t.path, slug: t.slug } : { path: t.path }) });
    const d = r.ok ? await r.json() as { summary?: string } : null;
    if (d?.summary) primeHoverCache(key, [d.summary]);
  } catch { /* the card keeps what the page says */ }
}

export const PEEK_KIND = "doc.peek";
export type PeekTarget = { path: string; slug?: string; title?: string; kind?: string };

/** The mark a hover card shows for an entity: a person's initials, otherwise the type's icon. */
export function entityMark(name: string, kind?: string): ReactNode {
  if (kind === "person" || kind === "company") return <Identity kind={kind} id={name} name={name} size={22} />;
  const c = (kind && ENTITY_CHIP[kind]) || DEFAULT_ENTITY_CHIP;
  return <span className="vx-hovercard-icon" data-kind={kind ?? "doc"}><Icon name={c.icon} size={14} /></span>;
}

/** `{ onActivate, peek, bind }`: call `onActivate` on the chip's click/Enter, spread `bind` on the
 *  chip, and render `peek` (the hover card; it portals) after it. */
export function usePeekOrOpen(target: PeekTarget | null): {
  onActivate: () => void; peek: ReactNode; open: boolean; bind: ReturnType<typeof useHoverCard>["bind"];
} {
  const openEntity = useOpenEntity();
  const name = target?.title || (target?.path.split("/").pop() ?? "").replace(/\.md$/i, "").replace(/[-_]+/g, " ");
  const hover = useHoverCard(target ? {
    name, mark: entityMark(name, target.kind), cacheKey: `${target.slug ?? ""}:${target.path}`,
    load: async () => {
      const md = String((await readWorkspaceFile(target.path, target.slug ? { slug: target.slug } : undefined)) ?? "");
      // NO STORY ON THE PAGE YET: ask for it once (the server writes it into the page), and show
      // what the page already says meanwhile — the next hover reads the stored story
      if (!pageSummary(md)) void backfillSummary(target, `${target.slug ?? ""}:${target.path}`);
      const story = hoverStory(md);
      return story ? [story] : hoverFacts(md, target.kind);
    },
  } : null);
  const go = () => { if (target) openEntity({ path: target.path, slug: target.slug }); };
  return { onActivate: go, peek: hover.card, open: false, bind: hover.bind };
}
