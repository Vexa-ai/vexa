/** DROPPING A PICTURE ONTO A PAGE (Vexa-ai/vexa#1612).
 *
 *  The founder's rule is one directory and one shape of reference, whoever puts the image there. So
 *  a drop or a paste in the page editor has to end in exactly what `fetch_asset` ends in: bytes in
 *  the workspace, and a RELATIVE reference at the cursor. The browser's own defaults are the
 *  opposite — a drop writes a `file://` path only that machine can resolve, and a paste of an image
 *  writes nothing at all.
 *
 *  These are the deciding functions, not the CodeMirror handler: the handler is three lines that
 *  need a live editor view, and everything that decides anything is here on purpose.
 */
import { describe, it, expect, vi } from "vitest";
import { assetName, filesFromTransfer, insertAt, referenceFor, storeDropped } from "../assetDrop";

const file = (name: string, type = "image/png") => new File([new Uint8Array([1, 2, 3])], name, { type });

describe("what the person handed us", () => {
  it("takes the dropped files in the order they were given", () => {
    const dt = { files: [file("a.png"), file("b.png")] } as unknown as DataTransfer;
    expect(filesFromTransfer(dt).map((f) => f.name)).toEqual(["a.png", "b.png"]);
  });

  it("takes a PASTED image, which arrives as an item and not as a file list", () => {
    const pasted = file("", "image/png");
    const dt = {
      files: [] as unknown as FileList,
      items: [{ kind: "string", getAsFile: () => null }, { kind: "file", getAsFile: () => pasted }],
    } as unknown as DataTransfer;
    expect(filesFromTransfer(dt)).toEqual([pasted]);
  });

  it("a plain-text drop carries no files, and is therefore not ours", () => {
    expect(filesFromTransfer({ files: [], items: [] } as unknown as DataTransfer)).toEqual([]);
    expect(filesFromTransfer(null)).toEqual([]);
  });

  it("names a pasted screenshot by the clock — every paste is `image.png` otherwise", () => {
    const at = new Date("2026-09-06T14:03:07Z");
    expect(assetName(file("", "image/png"), at)).toBe("pasted-20260906-140307.png");
    expect(assetName(file("chart.png"), at)).toBe("chart.png");   // a real name is kept
  });
});

describe("what goes in the page", () => {
  it("an image is an image and everything else is a link", () => {
    expect(referenceFor("assets/q3.png", "image/png", "q3.png")).toBe("![q3.png](assets/q3.png)");
    expect(referenceFor("assets/deck.pdf", "application/pdf", "deck.pdf")).toBe("[deck.pdf](assets/deck.pdf)");
  });

  it("the reference is RELATIVE — never the route it happens to be served by", () => {
    expect(referenceFor("assets/q3.png", "image/png", "q3.png")).not.toContain("/api/");
  });

  it("lands at the cursor, on its own line, without eating the paragraph it was dropped on", () => {
    const { value, cursor } = insertAt("one\ntwo", 3, 3, "![a](assets/a.png)");
    expect(value).toBe("one\n![a](assets/a.png)\ntwo");
    expect(value.slice(0, cursor)).toBe("one\n![a](assets/a.png)");   // typing resumes after it
  });

  it("replaces a selection rather than appending beside it", () => {
    expect(insertAt("before OLD after", 7, 10, "![a](assets/a.png)").value)
      .toBe("before \n![a](assets/a.png)\n after");
  });
});

describe("storing them", () => {
  it("uploads each file and hands back its reference, in order", async () => {
    const upload = vi.fn(async (_f: File, name: string) => ({ path: `assets/${name}`, content_type: "image/png" }));
    const { assets, failed } = await storeDropped([file("a.png"), file("b.png")], upload);
    expect(assets.map((a) => a.reference)).toEqual(["![a.png](assets/a.png)", "![b.png](assets/b.png)"]);
    expect(failed).toEqual([]);
  });

  it("REPORTS a file that would not store — a picture that vanishes silently is the whole bug", async () => {
    const upload = vi.fn(async (_f: File, name: string) => {
      if (name === "bad.png") throw new Error("413");
      return { path: `assets/${name}`, content_type: "image/png" };
    });
    const { assets, failed } = await storeDropped([file("bad.png"), file("good.png")], upload);
    expect(assets.map((a) => a.path)).toEqual(["assets/good.png"]);
    expect(failed).toEqual(["bad.png"]);
  });
});
