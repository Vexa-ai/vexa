"use client";
/** AN IMAGE FILE OPENS AS AN IMAGE (founder, 2026-10-15: "you need to render svg files"). A `.svg`,
 *  `.png`, `.jpg`, `.gif`, `.webp` … opened from Files is a picture, fitted to the panel, on a
 *  checkerboard so transparency shows, with zoom, a download, and (for an SVG) its source under "…".
 *
 *  NEVER INLINE: the picture is an `<img>` whose `src` is the workspace asset route, which serves
 *  the file as its own media type under `Content-Security-Policy: default-src 'none'; sandbox` — an
 *  SVG drawn by `<img>` runs no script, loads no external reference and renders no foreignObject
 *  content that could reach the page. Its source is shown as TEXT, never parsed into the DOM. */
import { useState } from "react";
import { workspaceAssetUrl } from "../ui-kit/docImages";

export const IMAGE_FILE = /\.(svg|png|jpe?g|gif|webp|avif|bmp|ico)$/i;
export const isImageFile = (path?: string | null) => IMAGE_FILE.test(String(path ?? ""));

const ZOOMS = [0.25, 0.5, 1, 2, 4];

export function ImageViewer({ path, slug, source }: { path: string; slug?: string; source?: string | null }) {
  const [zoom, setZoom] = useState<number | null>(null);          // null = fit to the panel
  const [showSource, setShowSource] = useState(false);
  const src = workspaceAssetUrl(path, slug ? { slug } : undefined);
  const name = path.split("/").pop() || path;
  const svg = /\.svg$/i.test(path);
  const step = (dir: 1 | -1) => {
    const cur = zoom ?? 1;
    const i = ZOOMS.findIndex((z) => z >= cur);
    setZoom(ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, (i < 0 ? ZOOMS.length - 1 : i) + dir))]);
  };
  return (
    <div className="vx-imgview" data-image-viewer="">
      <div className="vx-imgview-bar" role="toolbar" aria-label="Image">
        <button type="button" onClick={() => setZoom(null)} aria-pressed={zoom === null}>Fit</button>
        <button type="button" onClick={() => step(-1)} aria-label="Zoom out">−</button>
        <span className="vx-imgview-zoom">{zoom === null ? "Fit" : `${Math.round(zoom * 100)}%`}</span>
        <button type="button" onClick={() => step(1)} aria-label="Zoom in">+</button>
        <a className="vx-imgview-dl" href={src} download={name}>Download</a>
        {svg && source != null && (
          <button type="button" data-view-source="" aria-pressed={showSource} onClick={() => setShowSource((v) => !v)}>
            {showSource ? "View image" : "View source"}</button>
        )}
      </div>
      {showSource && svg
        ? <pre className="vx-imgview-source" data-image-source="">{source}</pre>
        : <div className="vx-imgview-stage" data-fit={zoom === null ? "" : undefined}>
            <img src={src} alt={name} data-image-path={path} draggable={false}
              style={zoom === null ? undefined : { width: `${zoom * 100}%`, maxWidth: "none" }} />
          </div>}
    </div>
  );
}
