"use client";
/** Obsidian-style markdown editor (CodeMirror 6 — the same engine Obsidian uses): the FILE stays
 *  plain markdown, but formatting renders live while you type — headings sized, bold bold, links
 *  colored, code mono-boxed. Syntax markers stay visible (live-preview marker-hiding is a later
 *  refinement); the point is that the text you edit already LOOKS like the document. */
import CodeMirror from "@uiw/react-codemirror";
import { EditorView } from "@codemirror/view";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { languages } from "@codemirror/language-data";
import { tags as t } from "@lezer/highlight";

const mdHighlight = HighlightStyle.define([
  { tag: t.heading1, fontSize: "1.55em", fontWeight: "650", fontFamily: "var(--sans)" },
  { tag: t.heading2, fontSize: "1.3em", fontWeight: "650", fontFamily: "var(--sans)" },
  { tag: t.heading3, fontSize: "1.15em", fontWeight: "600", fontFamily: "var(--sans)" },
  { tag: t.heading4, fontSize: "1.05em", fontWeight: "600", fontFamily: "var(--sans)" },
  { tag: t.strong, fontWeight: "700" },
  { tag: t.emphasis, fontStyle: "italic" },
  { tag: t.strikethrough, textDecoration: "line-through" },
  { tag: t.link, color: "var(--blue)", textDecoration: "underline" },
  { tag: t.url, color: "var(--blue)" },
  { tag: t.monospace, fontFamily: "var(--mono)", fontSize: "0.9em", color: "var(--accent)" },
  { tag: t.quote, color: "var(--t2)", fontStyle: "italic" },
  { tag: t.list, color: "var(--t1)" },
  { tag: t.meta, color: "var(--t3)" },           // the syntax markers: #, *, [, ] — recede
  { tag: t.processingInstruction, color: "var(--t3)" },
  { tag: t.contentSeparator, color: "var(--t3)" },
]);

const theme = EditorView.theme({
  "&": { background: "transparent", color: "var(--t1)", fontSize: "13px", height: "100%" },
  ".cm-scroller": { fontFamily: "var(--sans)", lineHeight: "1.65", padding: "14px 16px 40px" },
  ".cm-content": { caretColor: "var(--accent)", maxWidth: "740px" },
  "&.cm-focused": { outline: "none" },
  ".cm-cursor": { borderLeftColor: "var(--accent)" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": { background: "color-mix(in srgb, var(--accent) 22%, transparent) !important" },
  ".cm-activeLine": { background: "color-mix(in srgb, var(--sidebar) 55%, transparent)" },
  ".cm-line": { padding: "0 2px" },
}, { dark: true });

export function MarkdownEditor(p: { value: string; onChange: (v: string) => void }) {
  return (
    <CodeMirror
      value={p.value}
      onChange={p.onChange}
      autoFocus
      basicSetup={{ lineNumbers: false, foldGutter: false, highlightActiveLine: true, highlightSelectionMatches: false, searchKeymap: false }}
      extensions={[markdown({ base: markdownLanguage, codeLanguages: languages }), syntaxHighlighting(mdHighlight), theme, EditorView.lineWrapping]}
      style={{ flex: 1, minHeight: 0, overflow: "auto" }}
    />
  );
}
