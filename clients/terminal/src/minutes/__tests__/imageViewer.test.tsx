/** AN IMAGE FILE OPENS AS AN IMAGE, AND AN SVG NEVER RUNS (founder 2026-10-15: "you need to render
 *  svg files"). Opened from Files it is an `<img>` from the sandboxed asset route — never markup in
 *  our DOM — fitted, zoomable, downloadable; its source shows as text. png/jpg/gif/webp the same. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { ImageViewer, isImageFile } from "../ImageViewer";

afterEach(() => cleanup());

const EVIL = `<svg xmlns="http://www.w3.org/2000/svg" onload="window.__pwned=1"><script>window.__pwned=2</script>`
  + `<foreignObject><iframe src="https://evil.example/"></iframe></foreignObject><image href="https://evil.example/b.png"/></svg>`;

describe("an image file", () => {
  it("png, jpg, gif, webp and svg are pictures; a page is not", () => {
    for (const p of ["a.png", "b.jpg", "c.jpeg", "d.gif", "e.webp", "notes/assets/vexum.svg"]) expect(isImageFile(p), p).toBe(true);
    expect(isImageFile("notes/vexum.md")).toBe(false);
  });
  it("a malicious svg is an <img> from the asset route — no svg, script, iframe or foreign object in our DOM, nothing runs", () => {
    const { container } = render(<ImageViewer path="notes/assets/vexum.svg" source={EVIL} />);
    const img = container.querySelector("img")!;
    expect(img.getAttribute("src")).toBe("/api/workspace/asset?path=notes%2Fassets%2Fvexum.svg");
    expect(container.querySelector("svg, script, iframe, foreignObject")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    fireEvent.click(container.querySelector("[data-view-source]")!);
    expect(container.querySelector("[data-image-source]")?.textContent).toContain("<script>");   // as TEXT
    expect(container.querySelector("svg, script, iframe")).toBeNull();
  });
  it("fits by default, zooms, and downloads", () => {
    const { container, getByLabelText, getByText } = render(<ImageViewer path="a.png" slug="biz" />);
    expect(container.querySelector("[data-fit]")).not.toBeNull();
    fireEvent.click(getByLabelText("Zoom in"));
    expect(container.querySelector("[data-fit]")).toBeNull();
    expect(getByText("Download").getAttribute("href")).toBe("/api/workspace/asset?path=a.png&slug=biz");
    expect(container.querySelector("[data-view-source]")).toBeNull();          // a png has no source to show
  });
});
