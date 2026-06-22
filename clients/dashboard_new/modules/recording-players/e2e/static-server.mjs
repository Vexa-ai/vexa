/**
 * static-server.mjs — a zero-dependency static file server for the fixture.
 *
 * Why not file://? Chromium blocks ESM `import` from `file://` (origin "null" → CORS), and the fixture
 * page loads the players bundle + goldens as real ESM modules. Serving over http:// gives the page a
 * real origin AND mirrors how the page will be served on the real stack. Tiny + stdlib-only, so it adds
 * no dependency to the brick.
 *
 * Serves e2e/fixtures/ at http://127.0.0.1:<PORT> (PORT env or 4319). `/` → players-render.html.
 */
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, normalize, extname } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "fixtures");
const PORT = Number(process.env.PORT ?? 4319);

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
    if (pathname === "/" || pathname === "") pathname = "/players-render.html";
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
