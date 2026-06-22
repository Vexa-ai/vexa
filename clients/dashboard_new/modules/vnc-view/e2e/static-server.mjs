/**
 * static-server.mjs — a zero-dependency static file server for the VncView fixtures.
 *
 * Why not file://? Chromium blocks ESM `import` from `file://` (origin "null" → CORS), and the fixture
 * pages load the vnc-bundle + goldens as real ESM modules. Serving over http:// gives the page a real
 * origin AND mirrors how the page is served on the real stack. Tiny + stdlib-only, so it adds no
 * dependency to the brick.
 *
 * Serves this e2e/ dir at http://127.0.0.1:<PORT> (PORT env or 4318). `/` → vnc-url.html.
 */
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, normalize, extname } from "node:path";

const ROOT = dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT ?? 4318);

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

const server = createServer(async (req, res) => {
  try {
    let pathname = decodeURIComponent(new URL(req.url, "http://x").pathname);
    if (pathname === "/" || pathname === "") pathname = "/vnc-url.html";
    // contain to ROOT (no path traversal)
    const filePath = normalize(join(ROOT, pathname));
    if (!filePath.startsWith(ROOT)) {
      res.writeHead(403).end("forbidden");
      return;
    }
    const body = await readFile(filePath);
    res.writeHead(200, {
      "content-type": TYPES[extname(filePath)] ?? "application/octet-stream",
    });
    res.end(body);
  } catch {
    res.writeHead(404).end("not found");
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`fixture server: http://127.0.0.1:${PORT}/`);
});
