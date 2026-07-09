"""A minimal RFC6455 text WebSocket client (stdlib only).

Just enough to connect through the gateway `/ws`, send/receive masked text frames, and close — so
the transcript-dataflow proof needs no `websockets`/`websocket-client` dependency.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
from urllib.parse import urlparse


class WS:
    def __init__(self, url: str, *, timeout: float = 10.0, headers: dict | None = None):
        u = urlparse(url)
        self.host = u.hostname
        self.port = u.port or (443 if u.scheme == "wss" else 80)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
        )
        # Optional extra handshake headers (additive): the 0.10 /ws contract authenticates via the
        # X-API-Key HEADER as well as the api_key query param — compat callers pass it here.
        for h, v in (headers or {}).items():
            handshake += f"{h}: {v}\r\n"
        handshake += "\r\n"
        self.sock.sendall(handshake.encode())
        raw = self._read_until(b"\r\n\r\n")
        head, _sep, rest = raw.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"ws handshake failed: {raw[:200]!r}")
        # KEEP any bytes read past the 101 response header: a server that sends its first frame
        # immediately (e.g. an auto-subscribed channel) can land it in the SAME TCP segment as the
        # handshake reply — resetting the buffer here silently ATE that frame.
        self._buf = rest

    def _read_until(self, marker: bytes) -> bytes:
        data = b""
        while marker not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data

    def send_text(self, text: str) -> None:
        payload = text.encode()
        header = bytearray([0x81])  # FIN + text opcode
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv_text(self, *, timeout: float = 10.0) -> str:
        self.sock.settimeout(timeout)
        while True:
            frame = self._read_frame()
            if frame is None:
                raise TimeoutError("ws closed without a frame")
            opcode, payload = frame
            if opcode == 0x1:  # text
                return payload.decode(errors="replace")
            if opcode == 0x8:  # close
                raise ConnectionError("ws closed by peer")
            # ignore ping/pong/continuation for this harness

    def _read_frame(self):
        b0 = self._recv_exact(2)
        if b0 is None:
            return None
        opcode = b0[0] & 0x0F
        length = b0[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        payload = self._recv_exact(length) if length else b""
        return opcode, payload

    def _recv_exact(self, n: int):
        while len(self._buf) < n:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                return None if len(self._buf) < n else self._buf
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self) -> None:
        try:
            self.sock.sendall(b"\x88\x80" + os.urandom(4))  # masked close
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
