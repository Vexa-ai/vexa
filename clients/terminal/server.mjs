// Custom Next.js server with a real server-side WebSocket proxy for `/ws`.
//
// Why this exists: Next.js `rewrites()` proxy HTTP only — they do NOT proxy the
// WebSocket `upgrade` handshake. So a browser opening same-origin `wss://host/ws`
// never reaches the gateway. This server intercepts the HTTP `upgrade` event for
// path `/ws`, opens a *server-side* socket to the gateway with the `x-api-key`
// header (key stays server-side, never in any client-visible URL), and pipes
// frames bidirectionally. The browser connects KEYLESS to same-origin `/ws`.
import { createServer } from "node:http";
import next from "next";
import { WebSocketServer, WebSocket } from "ws";

const dev = process.env.NODE_ENV !== "production";
const port = parseInt(process.env.PORT || "3003", 10);
const hostname = process.env.HOST || "0.0.0.0";

const GATEWAY_URL = (process.env.GATEWAY_URL || "ws://127.0.0.1:18056")
  .replace(/\/$/, "")
  .replace(/^http/, "ws");
const BOT_KEY = process.env.VEXA_BOT_API_KEY || "";

const app = next({ dev, hostname, port });
const handle = app.getRequestHandler();

await app.prepare();

const server = createServer((req, res) => handle(req, res));

// Browser-facing WS server — we do the upgrade ourselves (noServer) only for `/ws`.
const wss = new WebSocketServer({ noServer: true });

server.on("upgrade", (req, socket, head) => {
  let pathname;
  try {
    pathname = new URL(req.url, `http://${req.headers.host}`).pathname;
  } catch {
    socket.destroy();
    return;
  }
  if (pathname !== "/ws") {
    // Let Next/HMR handle its own upgrades (e.g. `_next/webpack-hmr` in dev).
    return;
  }
  wss.handleUpgrade(req, socket, head, (client) => {
    proxyToGateway(client);
  });
});

function proxyToGateway(client) {
  const target = `${GATEWAY_URL}/ws`;
  const upstream = new WebSocket(target, {
    headers: BOT_KEY ? { "x-api-key": BOT_KEY } : {},
  });

  const pending = [];
  let upstreamOpen = false;

  client.on("message", (data, isBinary) => {
    if (upstreamOpen) upstream.send(data, { binary: isBinary });
    else pending.push([data, isBinary]);
  });

  upstream.on("open", () => {
    upstreamOpen = true;
    for (const [data, isBinary] of pending) upstream.send(data, { binary: isBinary });
    pending.length = 0;
  });
  upstream.on("message", (data, isBinary) => {
    if (client.readyState === WebSocket.OPEN) client.send(data, { binary: isBinary });
  });

  // Close each side when the other closes. Only forward a code if it's a valid
  // application close code (1000 / 3000-4999); reserved codes like 1005/1006
  // would throw, so fall back to a bare close.
  const fwd = (sock, code, reason) => {
    try {
      if (code === 1000 || (code >= 3000 && code <= 4999)) sock.close(code, reason);
      else sock.close();
    } catch {}
  };
  client.on("close", (code, reason) => fwd(upstream, code, reason));
  upstream.on("close", (code, reason) => fwd(client, code, reason));
  client.on("error", () => { try { upstream.close(); } catch {} });
  upstream.on("error", () => { try { client.close(); } catch {} });
}

server.listen(port, hostname, () => {
  // eslint-disable-next-line no-console
  console.log(`> Terminal ready on http://${hostname}:${port} (WS proxy /ws -> ${GATEWAY_URL}/ws)`);
});
