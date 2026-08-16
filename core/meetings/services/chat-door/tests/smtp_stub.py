"""A ~60-line in-process SMTP sink, so the send path is proven without Docker.

Docker runs on the build host, not here, and Python removed ``smtpd`` in 3.12 — so the test
lane owns a minimal server that speaks exactly the dialogue ``smtplib.send_message`` performs
(EHLO/HELO · MAIL FROM · RCPT TO · DATA · QUIT). It captures the raw message bytes, which is
what the assertions read. Against real Mailpit the same client code runs unchanged; only the
host/port differ.
"""
from __future__ import annotations

import socket
import threading
from dataclasses import dataclass, field


@dataclass
class Captured:
    mail_from: str = ""
    rcpt_to: list[str] = field(default_factory=list)
    data: str = ""


class SMTPStub:
    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self.messages: list[Captured] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "SMTPStub":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=3)

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn, conn.makefile("rwb") as stream:
            captured = Captured()
            stream.write(b"220 stub ESMTP\r\n")
            stream.flush()
            for raw in stream:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                upper = line.upper()
                if upper.startswith("EHLO"):
                    stream.write(b"250-stub\r\n250 HELP\r\n")
                elif upper.startswith("HELO"):
                    stream.write(b"250 stub\r\n")
                elif upper.startswith("MAIL FROM:"):
                    captured.mail_from = line.split(":", 1)[1].strip()
                    stream.write(b"250 OK\r\n")
                elif upper.startswith("RCPT TO:"):
                    captured.rcpt_to.append(line.split(":", 1)[1].strip())
                    stream.write(b"250 OK\r\n")
                elif upper == "DATA":
                    stream.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                    stream.flush()
                    body: list[str] = []
                    for data_raw in stream:
                        data_line = data_raw.decode("utf-8", "replace").rstrip("\r\n")
                        if data_line == ".":
                            break
                        body.append(data_line[1:] if data_line.startswith("..") else data_line)
                    captured.data = "\n".join(body)
                    self.messages.append(captured)
                    captured = Captured()
                    stream.write(b"250 OK queued\r\n")
                elif upper == "QUIT":
                    stream.write(b"221 Bye\r\n")
                    stream.flush()
                    return
                elif upper == "RSET":
                    captured = Captured()
                    stream.write(b"250 OK\r\n")
                else:
                    stream.write(b"250 OK\r\n")
                stream.flush()
