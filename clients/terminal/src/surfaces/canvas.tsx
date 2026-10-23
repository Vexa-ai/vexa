"use client";
import { registerCommand } from "../contributions";
import { LayoutServiceId, type TabDescriptor } from "../platform/layout";

// The "canvas" tab-kind itself (and the transcript widget) register in ../canvas/register.tsx: the
// canvas sits above this folder, so this file names the tab by kind and never imports it.
function canvasTab(): TabDescriptor {
  return { id: "meeting-canvas", title: "Meeting Canvas", kind: "canvas", params: {}, context: null };
}

registerCommand({
  id: "meeting.canvas.open",
  title: "Open Meeting Canvas",
  run: ({ container }) => container.get(LayoutServiceId).openTab(canvasTab()),
});
