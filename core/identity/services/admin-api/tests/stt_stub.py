"""Local STT HTTP fixture and wire-format readers; no external services."""
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import struct
import threading
import time


@contextmanager
def stt_stub(status=200, body=b'{"text":"hello"}', *, delay=0.0):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append({
                "path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": self.rfile.read(int(self.headers.get("Content-Length", "0"))),
            })
            time.sleep(delay)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # A timeout test closes the client before the delayed response.

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def closed_port_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


def parse_multipart(content_type, raw) -> dict:
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + raw
    )
    assert message.is_multipart()
    parts = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        if part.get_filename() is None:
            parts[name] = payload.decode()
        else:
            parts[name] = {
                "filename": part.get_filename(),
                "content_type": part.get_content_type(),
                "body": payload,
            }
    return parts


def wav_facts(raw: bytes) -> dict:
    facts = {"riff": raw[:4], "wave": raw[8:12]}
    offset = 12
    while offset + 8 <= len(raw):
        chunk, size = struct.unpack_from("<4sI", raw, offset)
        offset += 8
        if chunk == b"fmt ":
            fmt, channels, rate, _byte_rate, _align, bits = struct.unpack_from("<HHIIHH", raw, offset)
            facts.update(audio_format=fmt, channels=channels, sample_rate=rate, bits_per_sample=bits)
        elif chunk == b"data":
            facts["data_bytes"] = len(raw[offset:offset + size])
        offset += size + size % 2
    return facts
