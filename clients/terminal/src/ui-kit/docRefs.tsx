/** docRefs — what BOTH link renderers need: the contexts, the open-a-doc callback, and the chip
 *  vocabulary.
 *
 *  Extracted from `docLinks` when the second renderer arrived. `Wikilink` has to branch to `WsLink`
 *  (in-workspace vs cross-workspace — PRD decision 26.2), and `WsLink` needs the chip colours and
 *  the navigate callback, so leaving them in `docLinks` made the two files import each other. These
 *  primitives belong to neither renderer, which is why the cycle was a design signal rather than a
 *  lint failure: they are the vocabulary a link is rendered IN.
 *
 *  `docLinks` re-exports every name below, so nothing that already imported them changed.
 */
"use client";
import { createContext, useContext } from "react";
import { OPEN_ENTITY_EVENT } from "../canvas/actions";

// ── contexts ─────────────────────────────────────────────────────────────────────
/** `slug` (when the key is PRESENT) pins the target workspace — including `undefined`
 *  meaning the home workspace; when the key is absent the doc's own workspace applies.
 *
 *  `exact` is the stronger claim: this ref NAMES its workspace, so resolve there and NOWHERE else.
 *  A pinned slug is still only a starting point for the search (that is what lets a link inside a
 *  shared doc reach a sibling mount); an exact ref forbids the search outright. Only the workspace
 *  chip sets it, because only it is addressing a workspace rather than a document. */
export type DocNavigate = (detail: { path?: string; wikilink?: string; slug?: string; exact?: boolean }) => void;
/** Obsidian-style in-place navigation: the hosting doc pane provides a navigate fn so
 *  links replace the pane's content (with its own back/forward history). Outside a doc
 *  pane (chat, demo page) links fall back to opening a workbench tab. */
export const DocNavContext = createContext<DocNavigate | null>(null);
/** WHERE the rendering doc lives: its own workspace-relative path (base for relative
 *  links) and its workspace slug (undefined = the user's own workspace). Provided by
 *  the doc pane; empty in chat. */
export const DocMetaContext = createContext<{ path?: string; slug?: string }>({});

export function useOpenEntity(): DocNavigate {
  const nav = useContext(DocNavContext);
  const meta = useContext(DocMetaContext);
  return nav ?? ((detail) => {
    if (typeof window !== "undefined") {
      const slug = "slug" in detail ? detail.slug : meta.slug;
      window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, { detail: { ...detail, slug, docPath: meta.path } }));
    }
  });
}

// ── entity chip styling (mirrors the TYPE map in surfaces/entities.tsx) ───────────
export const ENTITY_CHIP: Record<string, { icon: string; color: string; bg: string }> = {
  person: { icon: "user", color: "var(--blue)", bg: "var(--bluebg)" },
  company: { icon: "building", color: "var(--accent)", bg: "var(--accentbg)" },
  organization: { icon: "web", color: "var(--violet)", bg: "var(--violetbg)" },
  project: { icon: "zap", color: "var(--green)", bg: "var(--greenbg)" },
  meeting: { icon: "cal", color: "var(--violet)", bg: "var(--violetbg)" },
  task: { icon: "tasks", color: "var(--green)", bg: "var(--greenbg)" },
  product: { icon: "zap", color: "var(--green)", bg: "var(--greenbg)" },
};
export const DEFAULT_ENTITY_CHIP = { icon: "link", color: "var(--blue)", bg: "var(--bluebg)" };

/** The ONE entity-kind → color lookup for the whole client (chips, inline transcript
 *  highlights, entity dots). Returns undefined for unknown kinds so each site picks its
 *  own fallback (chips default blue, transcript text defaults muted). */
export function entityColor(kind?: string): string | undefined {
  return kind ? ENTITY_CHIP[kind]?.color : undefined;
}

