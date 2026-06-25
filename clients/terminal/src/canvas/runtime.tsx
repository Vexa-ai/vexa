"use client";
import React, { Component, useCallback, useMemo, useRef, type ComponentType, type ErrorInfo, type ReactNode } from "react";
import { parse } from "acorn";
import { transform } from "sucrase";
import { ui } from "./kit";
import { useMeeting } from "./useMeeting";
import { useActions } from "./actions";
import { validateViewSource } from "./validator";

type AnyNode = { type: string; start: number; end: number; id?: { name?: string } | null; declaration?: AnyNode; [key: string]: unknown };
type Edit = { start: number; end: number; text: string };
type CompileResult = { ok: true; component: ComponentType; code: string } | { ok: false; errors: string[] };

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
      "useActions",
      "useState",
      "useMemo",
      "useEffect",
      `"use strict";\nconst exports = {}; let actions;\n${moduleCode}\nconst ViewComponent = (typeof exports !== "undefined" && exports.default) || (typeof Component !== "undefined" && Component);\nif (typeof ViewComponent !== "function") throw new Error("Meeting Canvas source must default-export a React component.");\nreturn function CanvasCompiledView(){ actions = useActions(); return React.createElement(ViewComponent); };`,
    );
    const component = fn(React, ui, useMeeting, useActions, React.useState, React.useMemo, React.useEffect) as ComponentType;
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

function FaultCard({ title, errors }: { title: string; errors: string[] }) {
  return (
    <div style={{ padding: 14 }}>
      <ui.Panel title={title} tone="warn">
        <ui.List items={errors.map((error) => ({ title: error, tone: "warn" as const }))} />
      </ui.Panel>
    </div>
  );
}

class RenderBoundary extends Component<{ resetKey: string; fallback: (error: Error) => ReactNode; children: ReactNode }, { error: Error | null; resetKey: string }> {
  state = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // React still logs the component stack in dev; the user-facing surface is below.
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

function RenderCandidate({ component: View, onSuccess }: { component: ComponentType; onSuccess: () => void }) {
  React.useEffect(onSuccess, [onSuccess]);
  return <View />;
}

function LastGood({ component: View }: { component: ComponentType }) {
  return (
    <div style={{ opacity: 0.72, pointerEvents: "none", marginTop: 12 }}>
      <View />
    </div>
  );
}

export function CanvasRuntime({ source }: { source: string }) {
  const compiled = useMemo(() => compile(source), [source]);
  const lastGood = useRef<ComponentType | null>(null);
  const markSuccess = useCallback(() => {
    if (compiled.ok) lastGood.current = compiled.component;
  }, [compiled]);

  if (!compiled.ok) return <FaultCard title={compiled.errors.some((e) => e.startsWith("line ")) ? "Canvas blocked" : "Canvas error"} errors={compiled.errors} />;

  return (
    <RenderBoundary
      resetKey={source}
      fallback={(error) => (
        <div style={{ padding: 14 }}>
          <ui.Panel title="Canvas render error" tone="warn">
            <ui.List items={[{ title: error.message || String(error), tone: "warn" }]} />
          </ui.Panel>
          {lastGood.current && lastGood.current !== compiled.component && <LastGood component={lastGood.current} />}
        </div>
      )}
    >
      <RenderCandidate component={compiled.component} onSuccess={markSuccess} />
    </RenderBoundary>
  );
}
