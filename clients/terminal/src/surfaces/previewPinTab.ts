"use client";

import { useEffect, useRef, type MouseEventHandler } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";

const PREVIEW_CLICK_DELAY_MS = 220;

export function usePreviewPinTab<T extends HTMLElement>(tab: TabDescriptor): {
  onClick: MouseEventHandler<T>;
  onDoubleClick: MouseEventHandler<T>;
} {
  const layout = useService(LayoutServiceId);
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (clickTimer.current) clearTimeout(clickTimer.current);
  }, []);

  return {
    onClick: () => {
      if (clickTimer.current) clearTimeout(clickTimer.current);
      clickTimer.current = setTimeout(() => {
        clickTimer.current = null;
        layout.openPreview(tab);
      }, PREVIEW_CLICK_DELAY_MS);
    },
    onDoubleClick: () => {
      if (clickTimer.current) {
        clearTimeout(clickTimer.current);
        clickTimer.current = null;
      }
      layout.openTab(tab);
    },
  };
}
