/** docDiagrams — what a ```mermaid fence renders as, in every renderer (Vexa-ai/vexa#1617).
 *
 *  Founder, 2026-09-06, asking the setup agent for the deployment picture: *"no we need to see
 *  diagram here"*. The agent wrote a `flowchart TB` into `_global/STRUCTURE.md`, said *"the diagram
 *  is now in STRUCTURE.md"*, and the page showed the fence as a code block. The admin saw source.
 *
 *  Three properties this module exists to hold, in the order they bite:
 *
 *  1. NO CDN, EVER. The mermaid library is an npm dependency compiled into our own bundle and served
 *     from our own origin. This product runs inside a bank's perimeter with no egress: a `<script
 *     src="https://cdn…">` renders nothing there, and the failure is invisible — a blank box on the
 *     one page someone opened to look at a diagram. It is also a third party watching a customer's
 *     documents load, which is the same rule `docImages` refuses to hotlink pictures under (#1612).
 *  2. PAGES WITHOUT DIAGRAMS PAY NOTHING. mermaid is ~1 MB of parser, layout and renderer. It is
 *     reached through a lazy `import("mermaid")` inside the effect, so the bundler emits it as its
 *     own chunk and no reader downloads it until a page actually carries a fence.
 *  3. NEVER A BLANK. The source block is not the failure case, it is the STARTING state: a fence
 *     renders as its own text and is REPLACED when (and only when) an SVG exists. A parse error
 *     leaves that text standing and prints the parser's message under it. So every path — loading,
 *     broken, a build where the import fails — shows the reader the diagram's source, which is the
 *     behaviour they had before this module and the worst thing it can degrade to.
 *
 *  THEME. The page renders light and dark from one CSS variable set (globals.css), so the diagram
 *  does too: mermaid's `base` theme with `themeVariables` resolved from the SAME `--t1/--t2/--t3/
 *  --panel/--panel2/--bg` tokens every other surface paints from. mermaid runs colour math on those
 *  values (khroma), so they are read as computed values, never handed through as `var(--t1)`.
 *
 *  SECURITY. kg/ markdown is agent-written from meeting transcripts and external content — the same
 *  untrusted input `MdxDoc`'s `assertNoExecutableMdx` refuses to execute. mermaid renders with
 *  `securityLevel: "strict"`, which sanitizes the SVG it returns and disables diagram click actions.
 */
"use client";
import { useEffect, useState, type CSSProperties } from "react";

export type DiagramTheme = "dark" | "light";

/** Does this fence's info string name a mermaid diagram? The first word is the language; anything
 *  after it is meta the renderers do not read (` ```mermaid title="x" ` stays a diagram). */
export function isMermaidFence(info: string | null | undefined): boolean {
  return (info ?? "").trim().split(/\s+/)[0].toLowerCase() === "mermaid";
}

// ── the library, loaded once and only when a page needs it ──────────────────────────
// The promise is cached at module scope rather than per-component: a page with six diagrams pulls
// the chunk once, and the second diagram's `await` resolves against the first one's download.
let loading: Promise<typeof import("mermaid").default> | null = null;
function loadMermaid(): Promise<typeof import("mermaid").default> {
  if (!loading) loading = import("mermaid").then((m) => m.default);
  return loading;
}

// ── the palette, read off the page ──────────────────────────────────────────────────
/** One design token, as the value the browser computed for it. Falls back to the literal from
 *  globals.css when there is no document to ask (tests, SSR) so a diagram is never colourless. */
function token(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch {
    return fallback;
  }
}

const FALLBACK: Record<DiagramTheme, Record<string, string>> = {
  dark: { "--bg": "#0e0e11", "--panel": "#222329", "--panel2": "#2b2c34", "--t1": "#ededf0", "--t2": "#9a9aa4", "--t3": "#65656f", "--accent": "#d8855c" },
  light: { "--bg": "#ffffff", "--panel": "#f7f7f9", "--panel2": "#e6e6eb", "--t1": "#1a1a1f", "--t2": "#5c5c66", "--t3": "#8a8a94", "--accent": "#c06a3f" },
};

/** mermaid `themeVariables` painted from the page's own tokens: TEXT is `--t1`/`--t2`, LINES and
 *  arrowheads are `--t2`, borders `--t3`, node fills `--panel2`, groups `--panel`. Every diagram
 *  family mermaid ships names its colours separately (a sequence diagram's actor box is not a
 *  flowchart's node), so the map is wide on purpose — an unnamed variable falls back to mermaid's
 *  own palette and reads as a foreign object on the page. */
function themeVariables(theme: DiagramTheme): Record<string, string | boolean> {
  const t = (name: string) => token(name, FALLBACK[theme][name]);
  const bg = t("--bg"), panel = t("--panel"), panel2 = t("--panel2");
  const t1 = t("--t1"), t2 = t("--t2"), t3 = t("--t3"), accent = t("--accent");
  return {
    darkMode: theme === "dark",              // drives mermaid's own lighten/darken direction
    background: bg,
    fontFamily: token("--sans", "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, system-ui, sans-serif"),
    fontSize: "14px",
    // text
    textColor: t1, primaryTextColor: t1, secondaryTextColor: t1, tertiaryTextColor: t2,
    titleColor: t1, nodeTextColor: t1, labelTextColor: t1, loopTextColor: t1,
    // lines
    lineColor: t2, arrowheadColor: t2, gridColor: t3, signalColor: t2, signalTextColor: t1,
    // fills + borders
    primaryColor: panel2, primaryBorderColor: t3,
    secondaryColor: panel, secondaryBorderColor: t3,
    tertiaryColor: panel, tertiaryBorderColor: t3,
    mainBkg: panel2, nodeBorder: t3, altBackground: panel,
    clusterBkg: panel, clusterBorder: t3,
    edgeLabelBackground: bg,
    noteBkgColor: panel, noteTextColor: t1, noteBorderColor: t3,
    actorBkg: panel2, actorBorder: t3, actorTextColor: t1, actorLineColor: t2,
    labelBoxBkgColor: panel2, labelBoxBorderColor: t3,
    sectionBkgColor: panel, sectionBkgColor2: panel2,
    taskBkgColor: panel2, taskBorderColor: t3, taskTextColor: t1,
    taskTextOutsideColor: t1, taskTextLightColor: t1, taskTextDarkColor: t1,
    activeTaskBkgColor: accent, activeTaskBorderColor: accent,
    doneTaskBkgColor: panel, doneTaskBorderColor: t3,
    critBorderColor: accent, critBkgColor: accent,
    errorBkgColor: panel, errorTextColor: t1,
  };
}

// A fresh DOM id per render: mermaid mounts the diagram under it while it measures, and two
// diagrams sharing an id measure each other.
let seq = 0;

/** Source → SVG, or a throw carrying the parser's own message. The `parse` call is deliberate and
 *  not redundant with `render`: it is the step whose error text names the line and token the author
 *  got wrong, which is the whole content of the fallback below. */
export async function renderMermaid(source: string, theme: DiagramTheme): Promise<string> {
  const mermaid = await loadMermaid();
  mermaid.initialize({
    startOnLoad: false,          // nothing scans the document; every render is one we asked for
    securityLevel: "strict",     // untrusted authorship — sanitize, and no click actions
    suppressErrorRendering: true, // a syntax error is OUR fallback to draw, not mermaid's bomb glyph
    theme: "base",
    themeVariables: themeVariables(theme),
  });
  await mermaid.parse(source);
  const { svg } = await mermaid.render(`vexa-mermaid-${++seq}`, source);
  return svg;
}

// ── the live theme ───────────────────────────────────────────────────────────────────
/** The theme as the DOM currently has it. `data-theme` on <html> (written by app/theme.ts, read by
 *  every rule in globals.css) is the one channel that is always current — `useTheme()` holds
 *  PER-CALLER state, so a diagram calling it would never hear the footer switch toggle somewhere
 *  else. Same reason, and the same shape, as MarkdownEditor's reader. */
export function useDocumentTheme(): DiagramTheme {
  const [theme, setTheme] = useState<DiagramTheme>("dark");
  useEffect(() => {
    const read = () => setTheme(document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark");
    read();
    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return theme;
}

// ── the render ───────────────────────────────────────────────────────────────────────
const SOURCE_BLOCK: CSSProperties = {
  fontFamily: "var(--mono)", fontSize: 12, background: "var(--panel2)", border: "1px solid var(--line)",
  borderRadius: 8, padding: "9px 11px", margin: "6px 0 10px", overflowX: "auto", lineHeight: 1.5, color: "var(--t1)",
};

/** The fence as it stands, optionally with what the parser said about it. This is what a reader sees
 *  while the library loads and what they keep if it never draws — the pre-#1617 behaviour, which is
 *  a legible diagram description, not a blank. */
function DiagramSource({ source, error }: { source: string; error?: string }) {
  return (
    <div data-mermaid-source style={{ margin: "6px 0 10px" }}>
      <pre style={{ ...SOURCE_BLOCK, margin: 0 }}><code>{source}</code></pre>
      {error && (
        <div data-mermaid-error role="note" style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--t3)", marginTop: 5 }}>
          diagram not drawn — {error}
        </div>
      )}
    </div>
  );
}

type DiagramState =
  | { status: "pending" }
  | { status: "ok"; svg: string }
  | { status: "failed"; error: string };

/** A ```mermaid fence, drawn. Used by BOTH doc renderers (MdxDoc's `pre` mapping and the plain
 *  Markdown fallback's fence branch) so a document that fails to compile as MDX still shows the
 *  picture — the same one-component rule `DocImage` follows for `![alt](src)`. */
export function MermaidDiagram({ source }: { source: string }) {
  const theme = useDocumentTheme();
  const [state, setState] = useState<DiagramState>({ status: "pending" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "pending" });
    renderMermaid(source, theme).then(
      (svg) => { if (!cancelled) setState({ status: "ok", svg }); },
      (err: unknown) => { if (!cancelled) setState({ status: "failed", error: String((err as Error)?.message ?? err).trim() }); },
    );
    return () => { cancelled = true; };
  }, [source, theme]);

  if (state.status !== "ok") return <DiagramSource source={source} error={state.status === "failed" ? state.error : undefined} />;
  return (
    // mermaid's own <style> is scoped to the svg's id, so the markup carries its colours with it and
    // nothing here leaks into the page. Horizontal overflow scrolls INSIDE the figure: a wide
    // deployment diagram must not widen the document (docs are read in a pane, not a browser tab).
    <div data-mermaid-diagram style={{ margin: "10px 0 14px", overflowX: "auto", lineHeight: "normal", textAlign: "center" }}
      dangerouslySetInnerHTML={{ __html: state.svg }} />
  );
}
