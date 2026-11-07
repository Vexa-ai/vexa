/** mermaid draws through the CSSOM (docDiagrams.tsx): the page refuses style ATTRIBUTES
 *  (`style-src-attr 'none'`), and mermaid writes them while it draws and in the SVG text it returns. */
import { describe, expect, it } from "vitest";
import { STYLE_DATA, drawingWithoutStyleAttributes, mermaidSvgNode } from "../docDiagrams";

describe("a mermaid draw without style attributes", () => {
  it("while it draws, setAttribute('style') goes through the CSSOM; after, setAttribute is the platform's", async () => {
    const real = Element.prototype.setAttribute;
    const el = document.createElement("div");
    await drawingWithoutStyleAttributes("m1", async () => {
      expect(Element.prototype.setAttribute).not.toBe(real);
      el.setAttribute("style", "color: red; font-size: 12px");
      el.setAttribute("title", "kept");
    });
    expect(el.style.color).toBe("red");
    expect(el.style.fontSize).toBe("12px");
    expect(el.getAttribute("title")).toBe("kept");
    expect(Element.prototype.setAttribute).toBe(real);
  });

  it("the text it serializes carries what the page applied in data-vx-style, and drops what it refused", async () => {
    const scratch = document.createElement("div");
    scratch.id = "dm2";
    document.body.appendChild(scratch);
    let html = "";
    await drawingWithoutStyleAttributes("m2", async () => {
      scratch.innerHTML = `<svg id="m2"><style>#m2{}</style><g><rect></rect><foreignObject><div><span style="color: red">x</span></div></foreignObject></g></svg>`;
      scratch.querySelector("rect")!.setAttribute("style", "fill: red");
      scratch.querySelector("div")!.setAttribute("style", "display: table-cell");
      // the label's markup style, as the browser leaves one it refused: an attribute, no declarations
      Object.defineProperty(scratch.querySelector("span")!, "style", { value: { cssText: "" } });
      html = scratch.innerHTML;
    });
    scratch.remove();
    expect(html).not.toMatch(/\sstyle="/);
    expect(html).toContain(`<rect ${STYLE_DATA}="fill: red;">`);
    expect(html).toContain(`${STYLE_DATA}="display: table-cell;"`);
    expect(html).toContain(`<span>x</span>`);
  });

  it("two draws at once: the patches stay until the last one ends", async () => {
    const real = Element.prototype.setAttribute;
    let release!: () => void;
    const first = drawingWithoutStyleAttributes("a", () => new Promise<void>((r) => { release = r; }));
    await drawingWithoutStyleAttributes("b", async () => {});
    expect(Element.prototype.setAttribute).not.toBe(real);
    release();
    await first;
    expect(Element.prototype.setAttribute).toBe(real);
  });

  it("a draw that throws still restores the platform", async () => {
    const real = Element.prototype.setAttribute;
    await expect(drawingWithoutStyleAttributes("c", async () => { throw new Error("parse"); })).rejects.toThrow("parse");
    expect(Element.prototype.setAttribute).toBe(real);
  });
});

describe("mermaid's SVG text as nodes", () => {
  it("applies each data-vx-style through the CSSOM and leaves no attribute of either kind", () => {
    const frag = mermaidSvgNode(`<svg id="m3"><style nonce="n">#m3{}</style><rect ${STYLE_DATA}="fill: red;"></rect><foreignObject><div ${STYLE_DATA}="display: table-cell;">x</div></foreignObject></svg>`);
    const rect = frag.querySelector("rect") as unknown as SVGElement;
    expect(rect.style.fill).toBe("red");
    expect((frag.querySelector("div") as HTMLElement).style.display).toBe("table-cell");
    expect(frag.querySelector(`[${STYLE_DATA}]`)).toBeNull();
    expect(frag.querySelector("svg")!.namespaceURI).toBe("http://www.w3.org/2000/svg");
    expect(frag.querySelector("style")!.getAttribute("nonce")).toBe("n");
  });
});
