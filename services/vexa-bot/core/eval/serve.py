#!/usr/bin/env python3
"""Range-capable static server so the browser can seek into the WAV files
(python's default http.server returns 200 for Range requests, which breaks
audio scrubbing / seek-on-click). Serves the samples dir on :42800."""
import os, re, http.server, socketserver

DIR = os.path.join(os.path.dirname(__file__), "samples")

class RangeHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=DIR, **k)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self):
        rng = self.headers.get("Range")
        if not rng:
            return super().send_head()
        path = self.translate_path(self.path)
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404)
            return None
        fs = os.fstat(f.fileno())
        size = fs.st_size
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        start = int(m.group(1)) if m.group(1) else 0
        end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_error(416)
            f.close()
            return None
        self.send_response(206)
        ctype = self.guess_type(path)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        f.seek(start)
        self._remaining = end - start + 1
        return f

    def copyfile(self, src, dst):
        rem = getattr(self, "_remaining", None)
        if rem is None:
            return super().copyfile(src, dst)
        while rem > 0:
            chunk = src.read(min(65536, rem))
            if not chunk:
                break
            try:
                dst.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                break
            rem -= len(chunk)

class TCP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

print("range-capable server on http://0.0.0.0:42800 serving", DIR, flush=True)
TCP(("0.0.0.0", 42800), RangeHandler).serve_forever()
