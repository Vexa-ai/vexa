/** WHAT A STATUS CARD OPENS (#1783, R3 `home2`): the workspace PAGE in the right panel (UI decision
 *  7c: documents never open in the centre; founder 2026-10-16: a page built from product components,
 *  not the markdown README). One seam, so every entry point (card, chip, Now strip) follows. */
import type { WorkspaceItem } from "../../../platform/desktop";
import { openDocOnRight } from "./acts";
import { featureOn } from "../../../platform/features";
import { HOME_TARGET_EVENT } from "../../../platform/events";

/** The panel address of a workspace's page: not a file; PagesPanel draws the page for it. */
export const WORKSPACE_PAGE_PATH = ".workspace";
/** "Expand": the shell widens the pages panel (and back). */
export const WORKSPACE_PAGE_WIDE_EVENT = "vexa:terminal:workspace-page-wide";
export const isWorkspacePagePath = (path: string | undefined | null): boolean => path === WORKSPACE_PAGE_PATH;

export function openWorkspaceView(w: Pick<WorkspaceItem, "slug" | "name"> & { write?: boolean }): Promise<void> {
  openDocOnRight(w.slug || "personal", WORKSPACE_PAGE_PATH, w.name);
  // shell2 (UI 7x): THE TARGET FOLLOWS THE WORKSPACE YOU OPEN — the home's composer now writes there.
  // `onlyHome`: a stored chat in front keeps its own target and stays in front (7d); a read-only
  // workspace never becomes the target.
  if (featureOn("shell2") && w.write !== false) {
    window.dispatchEvent(new CustomEvent(HOME_TARGET_EVENT, { detail: { workspace: w.slug && w.slug !== "personal" ? w.slug : "", onlyHome: true } }));
  }
  return Promise.resolve();
}
