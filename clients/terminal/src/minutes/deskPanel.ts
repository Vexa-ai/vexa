/** deskPanel — what the right panel opens when a chat names no document of its own.
 *
 *  PRD decision 26.4 (founder, 2026-09-02): *"let's make a default right sidebar page the personal
 *  desk readme and we will make the agent treat this as the actual desk."*
 *
 *  It used to open `_global/README.md` — the ORGANISATION's page — for a chat with no focus. That
 *  is the company's document, identical for everybody, and it is not what a person opening a fresh
 *  conversation is looking at their screen to find out. Their desk is: who and what is on it, what
 *  they owe, what is next, which rooms they are in. The agent maintains those sections
 *  (`shared/desk_readme.py`) precisely so this page is worth being the default.
 *
 *  And it is NAMED, from the registry (F49). The tab used to read `126` — the directory a desk
 *  happens to live in, printed at a person who has never seen that number before.
 *
 *  Scaffolded chats are untouched: a chat that carries `artifacts[]` renders those, and this is
 *  only the fallback.
 */
import { readActiveSet } from "../surfaces/workspaceApi";
import { primeWorkspaceNames, workspaceBySlug, workspaceLabel } from "../ui-kit/wsLinks";
import { WORKSPACE_WORD } from "./vocabulary";
import type { Page } from "./types";

/** The org tier and the private tier: always mounted, never chosen, so never a chosen tab. */
const IMPLICIT = new Set(["_global", "_system"]);
/** What this client calls the reader's own desk when it is addressed with no slug. */
export const PERSONAL = "personal";

/** The reader's own desk, named. Falls back to the product's word for it (`desk`) — which is a
 *  true label, unlike a subject id, when the registry has not answered yet. */
export async function deskLabel(): Promise<string> {
  try {
    const { subject } = await readActiveSet();
    if (!subject) return WORKSPACE_WORD;
    const rec = await workspaceBySlug(String(subject));
    return rec?.name || WORKSPACE_WORD;
  } catch {
    return WORKSPACE_WORD;                      // the panel must open even when the mount table does not
  }
}

/** The panel's tabs for a chat that opened without a scaffold focus.
 *
 *  Always leads with the reader's DESK README — including when the chat stresses group workspaces,
 *  because the desk is the page that says where they are — then a README tab per group, then the
 *  organisation tier last (it is the constant, and a constant belongs at the end of a tab strip). */
export async function deskPanelPages(workspaces: string[]): Promise<Page[]> {
  const shared = (workspaces ?? []).filter((w) => w && !IMPLICIT.has(w) && w !== PERSONAL);
  await primeWorkspaceNames(shared).catch(() => { /* labels degrade to slugs, never to a failure */ });
  const label = await deskLabel();
  const pages: Page[] = [{ path: "README.md", label }];
  for (const w of shared) pages.push({ path: "README.md", slug: w, label: workspaceLabel(w) });
  pages.push({ path: "README.md", slug: "_global", label: "_global" });
  return pages;
}
