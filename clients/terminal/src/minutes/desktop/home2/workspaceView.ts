/** WHAT A STATUS CARD OPENS (#1783, R3 `home2`): the workspace PAGE in the right panel (UI decision
 *  7c: documents never open in the centre; founder 2026-10-16: a page built from product components,
 *  not the markdown README). One seam, so every entry point (card, chip, Now strip) follows. */
import type { WorkspaceItem } from "../../../platform/desktop";
import { openDocOnRight } from "./acts";

/** The panel address of a workspace's page: not a file; PagesPanel draws the page for it. */
export const WORKSPACE_PAGE_PATH = ".workspace";
/** "Expand": the shell widens the pages panel (and back). */
export const WORKSPACE_PAGE_WIDE_EVENT = "vexa:terminal:workspace-page-wide";
export const isWorkspacePagePath = (path: string | undefined | null): boolean => path === WORKSPACE_PAGE_PATH;

export function openWorkspaceView(w: Pick<WorkspaceItem, "slug" | "name">): Promise<void> {
  openDocOnRight(w.slug || "personal", WORKSPACE_PAGE_PATH, w.name);
  return Promise.resolve();
}
