"use client";

import { type MouseEventHandler } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";

/**
 * VS Code preview/pin click behavior: single-click opens (or reuses) the shared
 * preview slot; double-click pins it as a persistent tab.
 *
 * We act IMMEDIATELY on each event — no setTimeout disambiguation. A deferred
 * single-click was vulnerable to unmount: if the click triggered a list
 * re-render that remounted the row, the cleanup cleared the pending timer and
 * the tab never opened ("I click and nothing happens"). openPreview/openTab
 * reconcile by tab id, so a double-click (which fires onClick → onClick →
 * onDoubleClick) harmlessly opens the preview, then promotes the same id to a
 * pinned tab.
 */
export function usePreviewPinTab<T extends HTMLElement>(tab: TabDescriptor): {
  onClick: MouseEventHandler<T>;
  onDoubleClick: MouseEventHandler<T>;
} {
  const layout = useService(LayoutServiceId);
  return {
    onClick: () => layout.openPreview(tab),
    onDoubleClick: () => layout.openTab(tab),
  };
}
