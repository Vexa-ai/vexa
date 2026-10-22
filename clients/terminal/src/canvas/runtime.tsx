"use client";
/** The Meeting Canvas runtime, on the terminal page: an ISOLATED frame and the bridge to it.
 *
 *  A view is workspace-written code, and the page that shows it holds the person's session. So the
 *  page never runs it. It mounts `/canvas-frame` — from the canvas origin when the deployment names
 *  one (`canvasFrameSrc`), else this origin — in an iframe sandboxed to `allow-scripts` alone: an
 *  opaque origin with its own policy that connects nowhere. It posts the view's source and the
 *  meeting in, and reads back only what `frame/protocol.ts` names, from that frame's window and its
 *  opaque origin alone: a status per source, the frame's height, and actions, each run against this
 *  page's real actions with its arguments coerced. */
import { useEffect, useMemo, useRef, useState } from "react";
import { ui } from "./kit";
import { useMeeting } from "./useMeeting";
import { useActions } from "./actions";
import { CANVAS_FRAME_SANDBOX, FRAME_MESSAGE_ORIGIN, canvasFrameSrc, plainState, readFromFrame, runAction } from "./frame/protocol";

type CanvasFailure = { source: string; errors: string[] };

function FailureNotice({ failure, onDismiss }: { failure: CanvasFailure; onDismiss: () => void }) {
  const [open, setOpen] = useState(false);
  const detail = failure.errors.filter(Boolean).join("\n");
  return (
    <div
      data-canvas-view-status="failed"
      data-canvas-view-error={detail}
      className="bd-strong bg-2 r-md pt-2 pr-2 pb-2 pl-2 mb-2 c-2 t-xs" style={{ minWidth: 0 }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <span className="r-full bg-danger" style={{ width: 7, height: 7, flex: "none" }} />
        <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>View update failed; keeping previous</span>
        <button type="button" onClick={() => setOpen((next) => !next)} className="bd-none bg-none c-accent t-xs p-0" style={{ cursor: "pointer" }}>{open ? "Hide" : "Details"}</button>
        <button type="button" onClick={onDismiss} className="bd-none bg-none c-3 t-xs p-0" style={{ cursor: "pointer" }}>Dismiss</button>
      </div>
      {open && <pre className="mt-2 mr-0 mb-0 ml-0 c-3 t-xs lh-snug" style={{ maxHeight: 150, overflow: "auto", whiteSpace: "pre-wrap" }}>{detail}</pre>}
    </div>
  );
}

function currentTheme(): string {
  return typeof document !== "undefined" && document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

export function CanvasRuntime({ source }: { source: string }) {
  const meeting = useMeeting();
  const actions = useActions();
  const frame = useRef<HTMLIFrameElement>(null);
  // read after mount: the layout's <meta> names the canvas origin, and the server render has no document
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => setSrc(canvasFrameSrc()), []);
  const [ready, setReady] = useState(false);
  const [height, setHeight] = useState(120);
  const [failure, setFailure] = useState<CanvasFailure | null>(null);
  const [dismissed, setDismissed] = useState("");
  const [gone, setGone] = useState(false);
  const state = useMemo(() => plainState(meeting), [meeting]);
  const sent = useRef<string | null>(null);
  // the latest actions the message handler runs, without re-subscribing it on every render
  const live = useRef(actions);
  live.current = actions;

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (!frame.current || e.source !== frame.current.contentWindow || e.origin !== FRAME_MESSAGE_ORIGIN) return;
      const m = readFromFrame(e.data);
      if (!m) return;
      if (m.type === "vexa-canvas:ready") { sent.current = null; setReady(true); return; }
      if (m.type === "vexa-canvas:size") { setHeight(Math.max(48, m.height)); return; }
      if (m.type === "vexa-canvas:status") {
        if (m.ok) setFailure((cur) => (cur?.source === m.source ? null : cur));
        else {
          setFailure({ source: m.source, errors: m.errors });
          console.warn("meeting canvas update failed", m.errors);
        }
        return;
      }
      runAction(live.current, m.name, m.args);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // THE FRAME LOADS ONCE. A second load means the view navigated its own document; whatever it
  // loaded is not the canvas, so the frame is taken down rather than shown.
  const loads = useRef(0);
  const onLoad = () => {
    loads.current += 1;
    if (loads.current > 1) {
      setGone(true);
      setFailure({ source, errors: ["The view navigated away from the canvas frame; it was closed."] });
    }
  };

  useEffect(() => {
    const win = frame.current?.contentWindow;
    if (!ready || !win) return;
    const theme = currentTheme();
    // "*": the frame's origin is opaque and cannot be named. The frame is the one this page mounted
    // from its own URL, and the page takes it down if it ever loads a second document (onLoad).
    if (sent.current !== source) {
      win.postMessage({ type: "vexa-canvas:render", source, state, theme }, "*");
      sent.current = source;
    } else {
      win.postMessage({ type: "vexa-canvas:state", state, theme }, "*");
    }
  }, [ready, source, state]);

  const showFailure = failure && dismissed !== failure.source;
  return (
    <div style={{ minWidth: 0, maxWidth: "100%" }}>
      {showFailure && <FailureNotice failure={failure} onDismiss={() => setDismissed(failure.source)} />}
      {gone ? (
        <ui.Empty title="Canvas view closed" body="The view left its frame. The agent can inspect the update note." />
      ) : src && (
        <iframe
          ref={frame}
          src={src}
          sandbox={CANVAS_FRAME_SANDBOX}
          title="Meeting Canvas view"
          referrerPolicy="no-referrer"
          onLoad={onLoad}
          data-canvas-frame=""
          className="bd-none"
          style={{ display: "block", width: "100%", height }}
        />
      )}
    </div>
  );
}
