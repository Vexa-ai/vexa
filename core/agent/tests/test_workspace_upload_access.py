"""WHO an attachment upload writes for, and what it refuses (`POST /api/workspace/upload`).

The terminal's chat attaches every dropped, pasted or picked file through this one route
(clients/terminal/src/surfaces/attachments.tsx). These pin the route's access rule from the
server side, where it is enforced: the subject comes from the authenticated `X-User-Id` the
gateway injected and nothing else, a request without one writes nothing, and a file over the
ceiling writes nothing.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from control_plane.api import create_app
from shared.config import load_settings
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from control_plane.routers import workspaces as ws_router

from tests.test_api import _FakeIdentity, _FakeRuntime


def _app(tmp_path, **kw):
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(tmp_path)), **kw,
    ))


def _written(tmp_path):
    return sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())


def test_an_upload_without_an_authenticated_subject_is_refused_and_writes_nothing(tmp_path):
    r = _app(tmp_path).post("/api/workspace/upload", files=[("files", ("a.txt", b"x", "text/plain"))])
    assert r.status_code == 401
    assert _written(tmp_path) == []


def test_an_upload_lands_only_in_the_callers_own_workspace(tmp_path):
    # a subject named in the body is not an identity: the gateway-injected header is
    r = _app(tmp_path).post("/api/workspace/upload", data={"subject": "u_jane"},
                            headers={"X-User-Id": "u_bob"},
                            files=[("files", ("notes.txt", b"plain", "text/plain"))])
    assert r.status_code == 200
    path = r.json()["files"][0]["path"]
    assert (tmp_path / "u_bob" / path).read_bytes() == b"plain"
    assert not (tmp_path / "u_jane").exists()


def test_an_upload_over_the_ceiling_is_refused_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ws_router, "MAX_UPLOAD_BYTES", 4)
    r = _app(tmp_path).post("/api/workspace/upload", headers={"X-User-Id": "u_bob"},
                            files=[("files", ("ok.txt", b"1234", "text/plain")),
                                   ("files", ("big.txt", b"12345", "text/plain"))])
    assert r.status_code == 413
    assert "big.txt" in r.json()["detail"]
    assert _written(tmp_path) == []   # all or nothing: the file under the ceiling is not kept either
