"use client";
import { useCallback, useEffect, useState } from "react";
import { registerCommand, registerTab, type TabProps } from "../contributions";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
import { CanvasActionsProvider } from "../canvas/actions";
import { CanvasRuntime } from "../canvas/runtime";
import { useMeeting } from "../canvas/useMeeting";

const SUBJECT = "u_live";
const VIEW_PATH = "views/meeting.tsx";
const FALLBACK_SOURCE = `
export default function Component() {
  return React.createElement(ui.Empty, { title: "No canvas view", body: "Create views/meeting.tsx in the workspace." });
}
`;

function canvasTab(): TabDescriptor {
  return { id: "meeting-canvas", title: "Meeting Canvas", kind: "canvas", params: {}, context: null };
}

async function readCanvasSource(): Promise<string> {
  try {
    const r = await fetch(`/api/workspace/file?subject=${SUBJECT}&path=${encodeURIComponent(VIEW_PATH)}`, { cache: "no-store" });
    if (!r.ok) return FALLBACK_SOURCE;
    const body = await r.json() as { content?: string };
    return body.content?.trim() ? body.content : FALLBACK_SOURCE;
  } catch {
    return FALLBACK_SOURCE;
  }
}

function CaptionStrip() {
  const meeting = useMeeting();
  const caption = meeting.transcript.liveCaption ?? meeting.transcript.segments[meeting.transcript.segments.length - 1]?.text ?? "Waiting for live meeting feed";
  return (
    <div style={{ minHeight: 30, flex: "none", borderTop: "1px solid var(--line)", background: "var(--sidebar)", color: "var(--t2)", fontSize: 12.5, display: "flex", alignItems: "center", gap: 8, padding: "0 12px" }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: meeting.transcript.liveCaption ? "var(--live)" : "var(--t3)", flex: "none" }} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{caption}</span>
    </div>
  );
}

function CanvasTab({ active }: TabProps) {
  const [source, setSource] = useState(FALLBACK_SOURCE);
  const [stamp, setStamp] = useState("");
  const load = useCallback(() => {
    void readCanvasSource().then((next) => {
      setSource(next);
      setStamp(new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    });
  }, []);

  useEffect(load, [load]);
  useEffect(() => {
    if (!active) return;
    load();
    const id = window.setInterval(load, 4000);
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [active, load]);

  return (
    <CanvasActionsProvider>
      <div style={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0, background: "var(--bg)" }}>
        <header style={{ height: 38, flex: "none", borderBottom: "1px solid var(--line)", background: "var(--sidebar)", display: "flex", alignItems: "center", gap: 10, padding: "0 12px" }}>
          <div style={{ color: "var(--t1)", fontSize: 13, fontWeight: 650 }}>Meeting Canvas</div>
          <div style={{ color: "var(--t3)", fontSize: 11.5, fontFamily: "var(--mono)" }}>{VIEW_PATH}</div>
          <div style={{ flex: 1 }} />
          {stamp && <div style={{ color: "var(--t3)", fontSize: 11.5 }}>reloaded {stamp}</div>}
        </header>
        <main style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          <div style={{ padding: 14 }}>
            <CanvasRuntime source={source} />
          </div>
        </main>
        <CaptionStrip />
      </div>
    </CanvasActionsProvider>
  );
}

registerTab("canvas", CanvasTab);
registerCommand({
  id: "meeting.canvas.open",
  title: "Open Meeting Canvas",
  run: ({ container }) => container.get(LayoutServiceId).openTab(canvasTab()),
});
