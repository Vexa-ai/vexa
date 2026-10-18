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
import { deskName } from "./vocabulary";
import { featureOn } from "../platform/features";
import { readActiveSet } from "../data/workspaceApi";
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
    if (!subject) return deskName();
    const rec = await workspaceBySlug(String(subject));
    return featureOn("shell2") ? deskName() : rec?.name || WORKSPACE_WORD;
  } catch {
    return deskName();                          // the panel must open even when the mount table does not
  }
}

/** The panel's tabs for a chat that opened without a scaffold focus.
 *
 *  Always leads with the reader's DESK README — including when the chat stresses group workspaces,
 *  because the desk is the page that says where they are — then a README tab per group. The company
 *  layer is NOT a tab here (founder, 2026-10-13): `_global` pages are reached in the Company profile
 *  only (ADR-0049), so an everyday account's strip never carries it. */
export async function deskPanelPages(workspaces: string[], target?: string): Promise<Page[]> {
  // THE ADMIN ACCOUNT HAS NO DESK (founder ruling 2026-10-11): its one page is the company layer's.
  // The active set says so (`admin_account: true`); an answer that does not say leaves the
  // everyday panel below, and the server refuses whatever that panel would not be allowed to read.
  try {
    if ((await readActiveSet()).admin_account === true) {
      return [{ path: "README.md", slug: "_global", label: "_global" }];
    }
  } catch { /* the everyday panel below — it degrades on its own */ }
  // THE DESK AND WHERE THE CHAT WRITES — not a README per mounted workspace (Vexa-ai/vexa#1783): a
  // chat reading eleven workspaces opened eleven tabs.
  const shared = (target !== undefined ? [target] : (workspaces ?? []))
    .filter((w) => w && !IMPLICIT.has(w) && w !== PERSONAL);
  await primeWorkspaceNames(shared).catch(() => { /* labels degrade to slugs, never to a failure */ });
  // THE DESK ONLY WHEN THE CHAT HAS IT (Vexa-ai/vexa#1783): a chat that writes elsewhere and does not
  // read Personal opens on where it writes, with no "Personal" tab.
  if (target !== undefined && target !== "" && target !== PERSONAL && !(workspaces ?? []).includes(PERSONAL)) {
    return shared.map((w) => ({ path: "README.md", slug: w, label: workspaceLabel(w) }));
  }
  const label = await deskLabel();
  const pages: Page[] = [{ path: "README.md", label }];
  for (const w of shared) pages.push({ path: "README.md", slug: w, label: workspaceLabel(w) });
  return pages;
}
