"use client";
import React, { Component, useCallback, useEffect, useMemo, useState, type ComponentType, type ErrorInfo, type ReactNode } from "react";
import { parse } from "acorn";
import { transform } from "sucrase";
import { ui } from "./kit";
import { useEntities, useMeeting, useMeetingDocs, useSignals, useSpeakers, useTranscript } from "./useMeeting";
import { useActions } from "./actions";
import { validateViewSource } from "./validator";

type AnyNode = { type: string; start: number; end: number; id?: { name?: string } | null; declaration?: AnyNode; [key: string]: unknown };
type Edit = { start: number; end: number; text: string };
type CompileResult = { ok: true; component: ComponentType; code: string } | { ok: false; errors: string[] };
type ActiveView = { source: string; component: ComponentType };
type CanvasFailure = { source: string; errors: string[] };

const cache = new Map<string, CompileResult>();

function applyEdits(code: string, edits: Edit[]): string {
  return [...edits].sort((a, b) => b.start - a.start).reduce((out, edit) => out.slice(0, edit.start) + edit.text + out.slice(edit.end), code);
}

function executableModule(code: string): string {
  const ast = parse(code, { ecmaVersion: "latest", sourceType: "module", ranges: true }) as unknown as { body: AnyNode[] };
  const edits: Edit[] = [];
  const appends: string[] = [];
  for (const node of ast.body) {
    if (node.type === "ImportDeclaration") {
      edits.push({ start: node.start, end: node.end, text: "" });
      continue;
    }
    if (node.type !== "ExportDefaultDeclaration") continue;
    const decl = node.declaration;
    if (!decl) continue;
    const declText = code.slice(decl.start, decl.end);
    if ((decl.type === "FunctionDeclaration" || decl.type === "ClassDeclaration") && decl.id?.name) {
      edits.push({ start: node.start, end: decl.start, text: "" });
      appends.push(`\nexports.default = ${decl.id.name};`);
    } else if (decl.type === "FunctionDeclaration") {
      edits.push({ start: node.start, end: node.end, text: `const Component = ${declText};\nexports.default = Component;` });
    } else if (decl.type === "ClassDeclaration") {
      edits.push({ start: node.start, end: node.end, text: `const Component = ${declText};\nexports.default = Component;` });
    } else {
      edits.push({ start: node.start, end: node.end, text: `exports.default = ${declText};` });
    }
  }
  return applyEdits(code, edits) + appends.join("");
}

function compile(source: string): CompileResult {
  const cached = cache.get(source);
  if (cached) return cached;

  const validation = validateViewSource(source);
  if (!validation.ok) {
    const blocked: CompileResult = { ok: false, errors: validation.errors };
    cache.set(source, blocked);
    return blocked;
  }

  try {
    const transpiled = transform(source, { transforms: ["typescript", "jsx"], jsxRuntime: "classic", production: false }).code;
    const moduleCode = executableModule(transpiled);
    const fn = new Function(
      "React",
      "ui",
      "useMeeting",
      "useTranscript",
      "useSpeakers",
      "useEntities",
      "useSignals",
      "useMeetingDocs",
      "useActions",
      "useState",
      "useMemo",
      "useEffect",
      `"use strict";\nconst exports = {}; let actions;\n${moduleCode}\nconst ViewComponent = (typeof exports !== "undefined" && exports.default) || (typeof Component !== "undefined" && Component);\nif (typeof ViewComponent !== "function") throw new Error("Meeting Canvas source must default-export a React component.");\nreturn function CanvasCompiledView(){ actions = useActions(); return React.createElement(ViewComponent); };`,
    );
    const component = fn(React, ui, useMeeting, useTranscript, useSpeakers, useEntities, useSignals, useMeetingDocs, useActions, React.useState, React.useMemo, React.useEffect) as ComponentType;
    const result: CompileResult = { ok: true, component, code: moduleCode };
    cache.set(source, result);
    return result;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const result: CompileResult = { ok: false, errors: [`Canvas compile failed: ${msg}`] };
    cache.set(source, result);
    return result;
  }
}

function failureText(errors: string[]): string {
  return errors.filter(Boolean).join("\n");
}

function FailureNotice({ failure, onDismiss }: { failure: CanvasFailure; onDismiss: () => void }) {
  const [open, setOpen] = useState(false);
  const detail = failureText(failure.errors);
  return (
    <div
      data-canvas-view-status="failed"
      data-canvas-view-error={detail}
      style={{ border: "1px solid var(--line2)", background: "var(--panel)", borderRadius: 8, padding: "8px 10px", marginBottom: 10, color: "var(--t2)", fontSize: 12, minWidth: 0 }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--live)", flex: "none" }} />
        <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>View update failed; keeping previous</span>
        <button type="button" onClick={() => setOpen((next) => !next)} style={{ border: "none", background: "transparent", color: "var(--accent)", fontSize: 12, cursor: "pointer", padding: 0 }}>{open ? "Hide" : "Details"}</button>
        <button type="button" onClick={onDismiss} style={{ border: "none", background: "transparent", color: "var(--t3)", fontSize: 12, cursor: "pointer", padding: 0 }}>Dismiss</button>
      </div>
      {open && <pre style={{ margin: "8px 0 0", maxHeight: 150, overflow: "auto", whiteSpace: "pre-wrap", color: "var(--t3)", fontSize: 11, lineHeight: 1.45 }}>{detail}</pre>}
    </div>
  );
}

class RenderBoundary extends Component<{ resetKey: string; fallback: (error: Error) => ReactNode; onError?: (error: Error) => void; children: ReactNode }, { error: Error | null; resetKey: string }> {
  state = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, _info: ErrorInfo): void {
    // React still logs the component stack in dev; the user-facing surface is below.
    this.props.onError?.(error);
  }

  componentDidUpdate(): void {
    if (this.state.resetKey !== this.props.resetKey) {
      this.setState({ error: null, resetKey: this.props.resetKey });
    }
  }

  render() {
    if (this.state.error) return this.props.fallback(this.state.error);
    return this.props.children;
  }
}

function RenderCandidate({ candidate, onSuccess }: { candidate: ActiveView; onSuccess: (candidate: ActiveView) => void }) {
  const View = candidate.component;
  React.useEffect(() => onSuccess(candidate), [candidate, onSuccess]);
  return <View />;
}

function HiddenTrial({ candidate, onSuccess, onError }: { candidate: ActiveView; onSuccess: (candidate: ActiveView) => void; onError: (candidate: ActiveView, error: Error) => void }) {
  return (
    <div aria-hidden="true" style={{ position: "fixed", left: -10000, top: -10000, width: 1, height: 1, overflow: "hidden", opacity: 0, pointerEvents: "none" }}>
      <RenderBoundary resetKey={`trial:${candidate.source}`} fallback={() => null} onError={(error) => onError(candidate, error)}>
        <RenderCandidate candidate={candidate} onSuccess={onSuccess} />
      </RenderBoundary>
    </div>
  );
}

export function CanvasRuntime({ source }: { source: string }) {
  const compiled = useMemo(() => compile(source), [source]);
  const [active, setActive] = useState<ActiveView | null>(null);
  const [pending, setPending] = useState<ActiveView | null>(null);
  const [failure, setFailure] = useState<CanvasFailure | null>(null);
  const [dismissedFailureSource, setDismissedFailureSource] = useState("");

  useEffect(() => {
    if (!compiled.ok) {
      setPending(null);
      setFailure({ source, errors: compiled.errors });
      console.warn("meeting canvas update failed", compiled.errors);
      return;
    }
    if (active?.source === source || pending?.source === source) return;
    setPending({ source, component: compiled.component });
  }, [active?.source, compiled, pending?.source, source]);

  const promote = useCallback((candidate: ActiveView) => {
    setActive(candidate);
    setPending((current) => current?.source === candidate.source ? null : current);
    setFailure((current) => current?.source === candidate.source ? null : current);
    setDismissedFailureSource("");
  }, []);

  const failTrial = useCallback((candidate: ActiveView, error: Error) => {
    const message = error.message || String(error);
    setPending((current) => current?.source === candidate.source ? null : current);
    setFailure({ source: candidate.source, errors: [`Canvas render failed: ${message}`] });
    console.warn("meeting canvas trial render failed", message);
  }, []);

  const activeError = useCallback((candidate: ActiveView, error: Error) => {
    const message = error.message || String(error);
    setFailure({ source: candidate.source, errors: [`Canvas render failed after promotion: ${message}`] });
    console.warn("meeting canvas visible render failed", message);
  }, []);

  const showFailure = failure && dismissedFailureSource !== failure.source;
  const Active = active?.component;

  return (
    <div style={{ minWidth: 0, maxWidth: "100%" }}>
      {showFailure && <FailureNotice failure={failure} onDismiss={() => setDismissedFailureSource(failure.source)} />}
      {Active ? (
        <RenderBoundary
          resetKey={`active:${active.source}`}
          onError={(error) => activeError(active, error)}
          fallback={() => <ui.Empty title="Canvas view paused" body="The last promoted view hit a render issue. The agent can inspect the update note." />}
        >
          <Active />
        </RenderBoundary>
      ) : (
        <ui.Empty title="Loading canvas view" body="The harness is checking the view before showing it." />
      )}
      {pending && <HiddenTrial candidate={pending} onSuccess={promote} onError={failTrial} />}
    </div>
  );
}
