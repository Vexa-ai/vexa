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

process.on("unhandledRejection", (reason) => {
  logError("unhandled promise rejection", reason);
});

process.on("uncaughtException", (err) => {
  logError("uncaught exception", err);
});

const app = next({ dev, hostname, port });
const handle = app.getRequestHandler();

await app.prepare();

const server = createServer((req, res) => {
  Promise.resolve(handle(req, res)).catch((err) => {
    logError("request handler failed", err);
    sendProxyError(res);
  });
});

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
  let closeOnSocketError = () => endSocket(socket);
  attachSocketError(socket, "client upgrade", () => closeOnSocketError());
  wss.handleUpgrade(req, socket, head, (client) => {
    closeOnSocketError = proxyToGateway(client, socket);
  });
});

server.on("clientError", (err, socket) => {
  logError("http client socket error", err);
  endSocket(socket);
});

server.on("error", (err) => {
  logError("http server error", err);
});

wss.on("error", (err) => {
  logError("websocket server error", err);
});

function proxyToGateway(client, clientSocket) {
  const target = `${GATEWAY_URL}/ws`;
  const upstream = new WebSocket(target, {
    headers: BOT_KEY ? { "x-api-key": BOT_KEY } : {},
  });

  const pending = [];
  let upstreamOpen = false;

  const closePair = () => {
    pending.length = 0;
    safeClose(client);
    safeClose(upstream);
  };
  const onProxyError = (scope, err) => {
    logError(scope, err);
    closePair();
  };

  attachSocketError(clientSocket || client._socket, "client websocket", (err) => onProxyError("client websocket socket error", err));
  attachSocketError(upstream._socket, "upstream websocket", (err) => onProxyError("upstream websocket socket error", err));

  client.on("message", (data, isBinary) => {
    if (upstreamOpen && upstream.readyState === WebSocket.OPEN) {
      sendFrame(upstream, data, { binary: isBinary }, "client -> upstream", closePair);
    } else if (upstream.readyState === WebSocket.CONNECTING) {
      pending.push([data, isBinary]);
    }
  });

  upstream.on("open", () => {
    upstreamOpen = true;
    attachSocketError(upstream._socket, "upstream websocket", (err) => onProxyError("upstream websocket socket error", err));
    for (const [data, isBinary] of pending) {
      sendFrame(upstream, data, { binary: isBinary }, "client -> upstream", closePair);
    }
    pending.length = 0;
  });
  upstream.on("upgrade", () => {
    attachSocketError(upstream._socket, "upstream websocket", (err) => onProxyError("upstream websocket socket error", err));
  });
  upstream.on("message", (data, isBinary) => {
    sendFrame(client, data, { binary: isBinary }, "upstream -> client", closePair);
  });
  upstream.on("unexpected-response", (_req, res) => {
    logError("upstream websocket rejected upgrade", new Error(`HTTP ${res.statusCode}`));
    closePair();
  });

  // Close each side when the other closes. Only forward a code if it's a valid
  // application close code (1000 / 3000-4999); reserved codes like 1005/1006
  // would throw, so fall back to a bare close.
  client.on("close", (code, reason) => safeClose(upstream, code, reason));
  upstream.on("close", (code, reason) => safeClose(client, code, reason));
  client.on("error", (err) => onProxyError("client websocket error", err));
  upstream.on("error", (err) => onProxyError("upstream websocket error", err));

  return closePair;
}

const socketErrorHandlers = new WeakSet();

function attachSocketError(socket, scope, onError) {
  if (!socket || socketErrorHandlers.has(socket)) return;
  socketErrorHandlers.add(socket);
  socket.on("error", (err) => {
    logError(scope, err);
    onError?.(err);
  });
}

function sendFrame(sock, data, options, scope, onError) {
  if (sock.readyState !== WebSocket.OPEN) return;
  try {
    sock.send(data, options, (err) => {
      if (!err) return;
      logError(`${scope} send failed`, err);
      onError?.(err);
    });
  } catch (err) {
    logError(`${scope} send failed`, err);
    onError?.(err);
  }
}

function safeClose(sock, code, reason) {
  if (!sock || sock.readyState === WebSocket.CLOSING || sock.readyState === WebSocket.CLOSED) return;
  try {
    if (code === 1000 || (code >= 3000 && code <= 4999)) sock.close(code, reason);
    else sock.close();
  } catch (err) {
    logError("websocket close failed", err);
  }
}

function sendProxyError(res) {
  if (res.destroyed || res.writableEnded) return;
  try {
    if (res.headersSent) {
      res.end();
      return;
    }
    res.writeHead(502, {
      "Content-Type": "application/json",
      "Cache-Control": "no-cache",
    });
    res.end(JSON.stringify({ error: "upstream_unavailable" }));
  } catch (err) {
    logError("failed to send proxy error response", err);
  }
}

function endSocket(socket) {
  if (!socket || socket.destroyed) return;
  try {
    socket.end("HTTP/1.1 400 Bad Request\r\n\r\n");
  } catch (err) {
    logError("socket end failed", err);
  }
}

function logError(scope, err) {
  // eslint-disable-next-line no-console
  console.error(`[terminal-server] ${scope}`, err);
}

server.listen(port, hostname, () => {
  // eslint-disable-next-line no-console
  console.log(`> Terminal ready on http://${hostname}:${port} (WS proxy /ws -> ${GATEWAY_URL}/ws)`);
});
