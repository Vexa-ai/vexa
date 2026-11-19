"""DockerBackend.start when `docker start` fails — fake socket, no daemon.

The container is created, then the daemon refuses to start it (the F2 storm case: a 12 MB env made
`docker start` answer 500). The backend removes the container it just created, right there, so a
failed start never leaves a `Created` container behind; the failure is still raised to the caller.
"""
from __future__ import annotations

import json
from urllib.parse import unquote, urlparse

import pytest

from runtime_kernel.docker_backend import DockerBackend
from runtime_kernel.profiles import Runnable


class _Resp:
    def __init__(self, status_code: int, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class _StartFailsSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def request(self, method: str, url: str, **kw):
        parsed = urlparse(url)
        path = unquote(parsed.path)
        path = path[path.index("/containers"):] if "/containers" in path else path
        q = f"?{parsed.query}" if parsed.query else ""
        self.calls.append(f"{method} {path}{q}")
        if method == "POST" and path == "/containers/create":
            return _Resp(201, {"Id": "cid-7"})
        if method == "POST" and path.endswith("/start"):
            return _Resp(500, {"message": "ResourceExhausted: trying to send message larger than max"})
        if method == "DELETE" and path.startswith("/containers/"):
            return _Resp(204)
        return _Resp(500, {"message": f"unhandled {method} {path}"})


def test_failed_start_removes_the_created_container():
    be = DockerBackend()
    fake = _StartFailsSession()
    be._session = fake

    with pytest.raises(RuntimeError, match="docker start vexa-w7 failed \\(500\\)"):
        be.start("w7", Runnable(image="alpine", command=["true"]), env={})

    assert fake.calls == [
        "POST /containers/create?name=vexa-w7",
        "POST /containers/cid-7/start",
        "DELETE /containers/cid-7?force=true",
    ]
