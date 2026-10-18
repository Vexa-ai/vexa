/** mermaidPreview — the ```mermaid fence, drawn under itself while you edit the page (#1617).
 *
 *  The pages editor is Obsidian-shaped: the FILE stays plain markdown and the formatting renders
 *  live in the same buffer you type into. A diagram is the case where that promise matters most —
 *  nobody can read a `flowchart TB` as text and know whether the arrows land where they meant — so
 *  the fence keeps its source AND gains its picture directly below the closing ```.
 *
 *  It is a BLOCK widget, which is why this is a facet computed from the state rather than a
 *  ViewPlugin: CodeMirror refuses plugin-provided decorations that change the vertical block
 *  structure, because the height map is built before plugins run.
 *
 *  The mechanics are here and not in `MarkdownEditor` for the reason `./assetDrop` exists — the
 *  editor component stays thin, and everything that DECIDES anything can be reached by a test
 *  without a live CodeMirror view.
 */
import { Decoration, EditorView, WidgetType, type DecorationSet } from "@codemirror/view";
import { isMermaidFence, renderMermaid, type DiagramTheme } from "../ui-kit/docDiagrams";

/** One drawn fence. `eq` is load-bearing: the decoration set is rebuilt on every keystroke, and
 *  without it every diagram in the document would be re-rendered (and visibly re-flow) while you
 *  type a sentence somewhere else. */
class MermaidWidget extends WidgetType {
  constructor(readonly source: string, readonly theme: DiagramTheme) { super(); }

  eq(other: MermaidWidget): boolean {
    return other.source === this.source && other.theme === this.theme;
  }

  toDOM(view: EditorView): HTMLElement {
    const box = document.createElement("div");
    box.setAttribute("data-mermaid-preview", "");
    box.style.cssText = "margin:2px 0 12px;overflow-x:auto;text-align:center;line-height:normal";
    // Nothing is drawn until the library resolves, and NOTHING IS LOST BY THAT: the source is on
    // screen one line above — this is an editor. So the pending state is an empty box rather than a
    // placeholder that would push the text the author is typing down and then up again.
    const measure = () => { try { view.requestMeasure(); } catch { /* view already torn down */ } };
    renderMermaid(this.source, this.theme).then(
      (svg) => { box.innerHTML = svg; measure(); },
      (err: unknown) => {
        box.setAttribute("data-mermaid-error", "");
        box.style.cssText += ";font-family:var(--mono);font-size:10.5px;color:var(--t3);text-align:left";
        box.textContent = `diagram not drawn — ${String((err as Error)?.message ?? err).trim()}`;
        measure();
      },
    );
    return box;
  }
}

/** Every mermaid fence in the source, as a widget placed after its closing ```. Exported for its
 *  test: it is a pure function of the text, so the scan can be checked with no editor at all. */
export function mermaidDecorations(text: string, theme: DiagramTheme): DecorationSet {
  const lines = text.split("\n");
  const ranges: ReturnType<Decoration["range"]>[] = [];
  let offset = 0;                                   // document position of the start of lines[i]
  for (let i = 0; i < lines.length; i++) {
    const open = /^\s*```(.*)$/.exec(lines[i]);
    if (!open) { offset += lines[i].length + 1; continue; }
    // Walk to the closing fence whether or not this one is a diagram — a ``` inside a bash block is
    // that block's content, and a scanner that does not consume it reads the rest of the file inside out.
    let end = offset + lines[i].length + 1;
    const body: string[] = [];
    let j = i + 1;
    for (; j < lines.length && !/^\s*```/.test(lines[j]); j++) { body.push(lines[j]); end += lines[j].length + 1; }
    if (j >= lines.length) break;                   // unterminated (still being typed) — nothing to draw
    end += lines[j].length;                         // the closing fence's own end-of-line position
    if (isMermaidFence(open[1]))
      ranges.push(Decoration.widget({ widget: new MermaidWidget(body.join("\n"), theme), block: true, side: 1 }).range(end));
    offset = end + 1;
    i = j;
  }
  return Decoration.set(ranges);
}

/** The editor extension. Recomputed from the document, so a fence draws as soon as it closes and
 *  redraws as its body changes; the theme is baked in, and MarkdownEditor rebuilds its extension
 *  list on a theme flip, which reconfigures the live view. */
export function mermaidPreview(theme: DiagramTheme) {
  return EditorView.decorations.compute(["doc"], (state) => mermaidDecorations(state.doc.toString(), theme));
}
