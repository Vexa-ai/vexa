/** "Make a card" for a chat's "…" and a page's "…" (#1783, desktop ADR D2; UI decisions 15c, 15d:
 *  "Pin to home" becomes "Make a card", a CRM card about that item; nothing is pinned outside the CRM).
 *
 *  Offered only while `desktop_cards` is on. The card is titled as the chat or the page and is about
 *  it (`chat:<id>` · `page:<workspace>/<path>`). It joins a workspace's board only when that is the
 *  page's (or the chat's) workspace AND the desktop says the person can write it; otherwise it is the
 *  person's own card. crm-api makes the person its member; the toast offers Open. */
import { useEffect, useState } from "react";
import { useFeature } from "../../platform/features";
import type { ContextMenuItem } from "../../ui-kit/ContextMenu";
import { makeCard } from "./cardsApi";
import type { Desktop } from "../../platform/desktop";
import { ensureSubject, knownSubject, loadDesktop, useDesktopState } from "./desktopApi";

/** A chat (its id, its title, the workspace it writes to) or a page (`slug` absent = the person's own). */
export type CardTarget =
  | { kind: "chat"; id: string; title: string; workspace?: string | null }
  | { kind: "page"; slug?: string | null; path: string; title: string };

export function useCardItems(): (t: CardTarget) => ContextMenuItem[] {
  const on = useFeature("desktop_cards");
  useDesktopState(on);
  const [desk, setDesk] = useState<string | null>(knownSubject());
  useEffect(() => { if (on) void ensureSubject().then(setDesk); }, [on]);
  // The desktop's workspace tiles say what the person may write (read at the moment of the choice).
  const writable = (desktop: Desktop | null, slug: string | null | undefined): string | null => {
    if (!slug || !desktop) return null;
    const ws = desktop.sections.find((s) => s.id === "workspaces")?.items
      .find((i) => i.kind === "workspace" && i.slug === slug);
    return ws && ws.kind === "workspace" && ws.write ? slug : null;
  };
  return (t) => {
    if (!on) return [];
    let about: string;
    let slug: string | null | undefined;
    if (t.kind === "chat") {
      if (!t.id) return [];
      about = `chat:${t.id}`;
      slug = t.workspace;
    } else {
      const own = t.slug || desk;
      const path = t.path.replace(/^\/+/, "");
      if (!own || !path) return [];
      about = `page:${own}/${path}`;
      slug = t.slug;
    }
    const title = (t.title || "").trim() || (t.kind === "page" ? t.path : "Untitled chat");
    return [{ id: "make-card", label: "Make a card", detail: "A CRM card about this, on your desktop",
      onSelect: async () => { await makeCard({ title, about, workspace: writable(await loadDesktop(), slug) }); } }];
  };
}
