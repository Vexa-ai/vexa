/** Compile a Meeting Canvas view's source into a React component — INSIDE THE FRAME ONLY.
 *
 *  This is the one place a view's text becomes code (`new Function`), and it is imported by the
 *  frame document alone (`frame/CanvasFrame.tsx`, served at `/canvas-frame` in an opaque-origin
 *  sandbox). The terminal page never evaluates a view: `runtime.tsx` posts the source across.
 *  `validateViewSource` still runs first, as the author's feedback on what the harness supports —
 *  it is a lint, not the boundary; the boundary is the frame. */
import React, { type ComponentType } from "react";
import { parse } from "acorn";
import { transform } from "sucrase";
import { ui } from "../kit";
import { useEntities, useMeeting, useMeetingDocs, useSignals, useSpeakers, useTranscript } from "../useMeeting";
import { useActions } from "../actions";
import { validateViewSource } from "../validator";

type AnyNode = { type: string; start: number; end: number; id?: { name?: string } | null; declaration?: AnyNode; [key: string]: unknown };
type Edit = { start: number; end: number; text: string };
export type CompileResult = { ok: true; component: ComponentType } | { ok: false; errors: string[] };

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
    } else if (decl.type === "FunctionDeclaration" || decl.type === "ClassDeclaration") {
      edits.push({ start: node.start, end: node.end, text: `const Component = ${declText};\nexports.default = Component;` });
    } else {
      edits.push({ start: node.start, end: node.end, text: `exports.default = ${declText};` });
    }
  }
  return applyEdits(code, edits) + appends.join("");
}

export function compileView(source: string): CompileResult {
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
      "React", "ui", "useMeeting", "useTranscript", "useSpeakers", "useEntities", "useSignals",
      "useMeetingDocs", "useActions", "useState", "useMemo", "useEffect",
      `"use strict";\nconst exports = {}; let actions;\n${moduleCode}\nconst ViewComponent = (typeof exports !== "undefined" && exports.default) || (typeof Component !== "undefined" && Component);\nif (typeof ViewComponent !== "function") throw new Error("Meeting Canvas source must default-export a React component.");\nreturn function CanvasCompiledView(){ actions = useActions(); return React.createElement(ViewComponent); };`,
    );
    const component = fn(React, ui, useMeeting, useTranscript, useSpeakers, useEntities, useSignals, useMeetingDocs, useActions, React.useState, React.useMemo, React.useEffect) as ComponentType;
    const result: CompileResult = { ok: true, component };
    cache.set(source, result);
    return result;
  } catch (err) {
    const result: CompileResult = { ok: false, errors: [`Canvas compile failed: ${err instanceof Error ? err.message : String(err)}`] };
    cache.set(source, result);
    return result;
  }
}
