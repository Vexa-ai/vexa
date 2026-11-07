"use client";
/** The Meeting Canvas FRAME — the document a view runs in (`/canvas-frame`, opaque-origin sandbox).
 *
 *  It waits for the page that embeds it to post a view's source and the meeting, compiles the
 *  source (`compile.ts`), trial-renders it hidden, and only then shows it — the last good view
 *  stays up when a new one fails. Everything it reports goes back by `postMessage` to its parent:
 *  the status of each source, its own height, and every action a person pressed. It accepts
 *  messages from its parent window only, and only when they come from the terminal's origin
 *  (`parentOriginsFor`); it posts to that origin by name, and it can reach nothing else. */
import { Component, useCallback, useEffect, useMemo, useRef, useState, type ComponentType, type ErrorInfo, type ReactNode } from "react";
import { ui } from "../kit";
import { HarnessActionsProvider } from "../actions";
import { MeetingStateProvider } from "../useMeeting";
import type { HarnessActions, MeetingState } from "../types";
import { compileView } from "./compile";
import { ACTION_NAMES, parentOriginsFor, readToFrame, type ActionName, type FromFrame } from "./protocol";
import { lockFrame } from "./lockdown";

// Before anything else in the frame's realm, and long before a view is compiled (lockdown.ts).
lockFrame();

type ActiveView = { source: string; component: ComponentType };

/** The terminal origins this frame answers to, fixed once for the document. */
let parents: string[] = [];

function post(msg: FromFrame): void {
  // To the parent window, and only while the parent is the terminal: a message is delivered to the
  // one named origin the parent actually has, and to nothing else.
  for (const origin of parents) window.parent.postMessage(msg, origin);
}

/** `HarnessActions` as messages to the page — the view's only way to cause anything. */
function bridgeActions(): HarnessActions {
  const send = (name: ActionName) => (...args: unknown[]) => post({ type: "vexa-canvas:action", name, args });
  return Object.fromEntries(ACTION_NAMES.map((n) => [n, send(n)])) as unknown as HarnessActions;
}

class RenderBoundary extends Component<{ resetKey: string; fallback: ReactNode; onError?: (error: Error) => void; children: ReactNode }, { error: Error | null; resetKey: string }> {
  state = { error: null as Error | null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, _info: ErrorInfo): void {
    this.props.onError?.(error);
  }

  componentDidUpdate(): void {
    if (this.state.resetKey !== this.props.resetKey) this.setState({ error: null, resetKey: this.props.resetKey });
  }

  render() {
    return this.state.error ? this.props.fallback : this.props.children;
  }
}

function RenderCandidate({ candidate, onSuccess }: { candidate: ActiveView; onSuccess: (c: ActiveView) => void }) {
  const View = candidate.component;
  useEffect(() => onSuccess(candidate), [candidate, onSuccess]);
  return <View />;
}

export function CanvasFrame({ parentOrigins = [] }: { parentOrigins?: readonly string[] }) {
  parents = parentOriginsFor(parentOrigins, typeof document === "undefined" ? "" : document.URL);
  const [source, setSource] = useState<string | null>(null);
  const [state, setState] = useState<MeetingState | null>(null);
  const [active, setActive] = useState<ActiveView | null>(null);
  const [pending, setPending] = useState<ActiveView | null>(null);
  const actions = useMemo(bridgeActions, []);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.source !== window.parent || window.parent === window || !parents.includes(e.origin)) return;
      const m = readToFrame(e.data);
      if (!m) return;
      document.documentElement.setAttribute("data-theme", m.theme);
      setState(m.state);
      if (m.type === "vexa-canvas:render") setSource(m.source);
    };
    window.addEventListener("message", onMessage);
    post({ type: "vexa-canvas:ready" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // A source that failed once is not tried again: the same text fails the same way, and retrying it
  // on every render is a loop. A new source (the agent's next write) gets its own trial.
  const failed = useRef(new Set<string>());

  useEffect(() => {
    if (source === null || active?.source === source || pending?.source === source || failed.current.has(source)) return;
    const compiled = compileView(source);
    if (!compiled.ok) {
      failed.current.add(source);
      post({ type: "vexa-canvas:status", source, ok: false, errors: compiled.errors });
      return;
    }
    setPending({ source, component: compiled.component });
  }, [active?.source, pending?.source, source]);

  const promote = useCallback((candidate: ActiveView) => {
    setActive(candidate);
    setPending((cur) => (cur?.source === candidate.source ? null : cur));
    post({ type: "vexa-canvas:status", source: candidate.source, ok: true, errors: [] });
  }, []);

  const fail = useCallback((candidate: ActiveView, error: Error, when: string) => {
    failed.current.add(candidate.source);
    setPending((cur) => (cur?.source === candidate.source ? null : cur));
    post({ type: "vexa-canvas:status", source: candidate.source, ok: false, errors: [`Canvas render failed${when}: ${error.message || String(error)}`] });
  }, []);

  // The page sizes the iframe to the view: the frame says how tall it is whenever that changes.
  useEffect(() => {
    const el = root.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => post({ type: "vexa-canvas:size", height: el.scrollHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (!state) return <div ref={root} />;
  const Active = active?.component;
  return (
    <div ref={root} style={{ minWidth: 0, maxWidth: "100%" }}>
      <MeetingStateProvider state={state}>
        <HarnessActionsProvider value={actions}>
          {Active ? (
            <RenderBoundary resetKey={`active:${active.source}`} onError={(e) => fail(active, e, " after promotion")}
              fallback={<ui.Empty title="Canvas view paused" body="The last promoted view hit a render issue. The agent can inspect the update note." />}>
              <Active />
            </RenderBoundary>
          ) : (
            <ui.Empty title="Loading canvas view" body="The harness is checking the view before showing it." />
          )}
          {pending && (
            <div aria-hidden="true" style={{ position: "fixed", left: -10000, top: -10000, width: 1, height: 1, overflow: "hidden", opacity: 0, pointerEvents: "none" }}>
              <RenderBoundary resetKey={`trial:${pending.source}`} fallback={null} onError={(e) => fail(pending, e, "")}>
                <RenderCandidate candidate={pending} onSuccess={promote} />
              </RenderBoundary>
            </div>
          )}
        </HarnessActionsProvider>
      </MeetingStateProvider>
    </div>
  );
}

export default CanvasFrame;
