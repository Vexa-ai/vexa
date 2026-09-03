"use client";
/** Obsidian-style markdown editor (CodeMirror 6 — the same engine Obsidian uses): the FILE stays
 *  plain markdown, but formatting renders live while you type — headings sized, bold bold, links
 *  colored, code mono-boxed. Syntax markers stay visible (live-preview marker-hiding is a later
 *  refinement); the point is that the text you edit already LOOKS like the document.
 *
 *  THEMING — the editor paints from the SAME var(--*) tokens as the read view it replaces, and
 *  follows the live `data-theme` on <html> that src/app/theme.ts writes. Two traps, both of which
 *  shipped:
 *
 *   1. `@uiw/react-codemirror`'s `theme` prop DEFAULTS TO "light", which pushes its
 *      `defaultLightThemeOption` — literally `&{ backgroundColor: "#fff" }` with `{ dark: false }`.
 *      Our own theme said `background: "transparent"`, a SHORTHAND against their LONGHAND, so the
 *      hard `#fff` won: a white sheet under #ededf0 dark-theme text — near-white on white, which
 *      is exactly what the editor looked like in dark mode. Passing `theme="none"` is the only way
 *      to stop that extension being injected; ours is then the only theme in play, and it sets
 *      `backgroundColor` (longhand) explicitly rather than relying on transparency.
 *   2. `{ dark }` is not decoration — it feeds EditorView.darkTheme, which drives CodeMirror's own
 *      &dark/&light rules (selection layer, drop cursor, tooltips, panels). It was hardcoded
 *      `true`, so day mode got the dark variants. It now tracks the live theme.
 */
import { useEffect, useMemo, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { EditorView } from "@codemirror/view";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { languages } from "@codemirror/language-data";
import { tags as t } from "@lezer/highlight";
import type { Theme } from "../app/theme";

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

/** Every colour resolves from the token set, so ONE theme object serves both modes — the tokens
 *  swap under :root[data-theme="light"] and the editor repaints with the rest of the panel.
 *  `--bg` is the pages panel's own ground (tokens.ts `surface.pages`), so edit and read sit on
 *  exactly the same sheet and the mode switch is invisible. */
const editorTheme = (dark: boolean) => EditorView.theme({
  "&": { backgroundColor: "var(--bg)", color: "var(--t1)", fontSize: "13px", height: "100%" },
  ".cm-scroller": { fontFamily: "var(--sans)", lineHeight: "1.65", padding: "14px 16px 40px" },
  ".cm-content": { caretColor: "var(--accent)", maxWidth: "740px" },
  "&.cm-focused": { outline: "none" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": { background: "color-mix(in srgb, var(--accent) 22%, transparent) !important" },
  ".cm-activeLine": { background: "color-mix(in srgb, var(--sidebar) 55%, transparent)" },
  ".cm-gutters": { backgroundColor: "var(--bg)", color: "var(--t3)", border: "none" },
  ".cm-panels, .cm-tooltip": { backgroundColor: "var(--panel)", color: "var(--t1)", border: "1px solid var(--line)" },
  ".cm-line": { padding: "0 2px" },
}, { dark });

/** The LIVE theme, read off the one place that is always current: the `data-theme` attribute
 *  src/app/theme.ts stamps on <html> (and globals.css keys every other surface off). `useTheme()`
 *  holds PER-CALLER state, so an editor calling it would never hear the footer switch toggle in a
 *  different component — the attribute is the shared channel, so observe that. */
function useDocumentTheme(): Theme {
  const [theme, setTheme] = useState<Theme>("dark");
  useEffect(() => {
    const read = () => setTheme(document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark");
    read();
    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return theme;
}

export function MarkdownEditor(p: { value: string; onChange: (v: string) => void }) {
  const theme = useDocumentTheme();
  // New identity on a theme flip → @uiw reconfigures the live view, so the switch repaints the
  // editor in place instead of only on the next mount.
  const extensions = useMemo(
    () => [markdown({ base: markdownLanguage, codeLanguages: languages }), syntaxHighlighting(mdHighlight), editorTheme(theme === "dark"), EditorView.lineWrapping],
    [theme],
  );
  return (
    <CodeMirror
      value={p.value}
      onChange={p.onChange}
      autoFocus
      // "none" — do NOT let the library inject its default light theme (see the header note).
      theme="none"
      basicSetup={{ lineNumbers: false, foldGutter: false, highlightActiveLine: true, highlightSelectionMatches: false, searchKeymap: false }}
      extensions={extensions}
      style={{ flex: 1, minHeight: 0, overflow: "auto" }}
    />
  );
}
