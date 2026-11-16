"""The chart's postgres-password hook (runtime_kernel.pg_password_rotate), offline: the API calls it
makes (Secret get/patch, Deployment restart), the exec it runs in the postgres Pod (the password on
stdin only), and that every step can run again after a failure."""
from __future__ import annotations

import base64
import json

import pytest

from runtime_kernel import k8s_backend
from runtime_kernel import pg_password_rotate as rot

NS, SECRET, POD = "vexa", "postgres-credentials", "vexa-vexa-postgres-0"
SECRET_PATH = f"/api/v1/namespaces/{NS}/secrets/{SECRET}"


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


class Cluster:
    """The Secret, the Deployments' restart stamps, and every call — answered as the API server does."""

    def __init__(self, data: dict[str, str]):
        self.data = {k: _b64(v) for k, v in data.items()}
        self.calls: list[tuple] = []
        self.restarted: list[str] = []

    def __call__(self, method, path, *, body=None, params=None, content_type=None):
        self.calls.append((method, path, body, content_type))
        if path == SECRET_PATH and method == "GET":
            return 200, {"kind": "Secret", "data": dict(self.data)}
        if path == SECRET_PATH and method == "PATCH" and content_type == "application/merge-patch+json":
            self.data.update(body["data"])
            return 200, {}
        if path == SECRET_PATH and method == "PATCH" and content_type == "application/json-patch+json":
            for op in body:
                assert op["op"] == "remove"
                self.data.pop(op["path"].rsplit("/", 1)[1])
            return 200, {}
        if path.startswith(f"/apis/apps/v1/namespaces/{NS}/deployments/") and method == "PATCH":
            assert content_type == "application/merge-patch+json"
            assert "kubectl.kubernetes.io/restartedAt" in body["spec"]["template"]["metadata"]["annotations"]
            self.restarted.append(path.rsplit("/", 1)[1])
            return 200, {}
        raise AssertionError(f"unexpected {method} {path}")

    def field(self, key):
        return base64.b64decode(self.data[key]).decode() if key in self.data else None


@pytest.fixture
def cluster(monkeypatch):
    c = Cluster({"POSTGRES_PASSWORD": "postgres", "POSTGRES_USER": "vexa", "POSTGRES_DB": "vexa"})
    monkeypatch.setattr(k8s_backend, "_api", c)
    monkeypatch.setattr(rot.time, "sleep", lambda s: None)
    return c


def test_a_rotation_sets_the_role_then_the_secret_then_restarts_and_clears_next(cluster, monkeypatch):
    execs = []

    def fake_exec(ns, pod, container, command, stdin, timeout=60):
        execs.append((ns, pod, container, command, stdin))
        assert cluster.field("POSTGRES_PASSWORD") == "postgres"       # the Secret moves only after the role
        return 0
    monkeypatch.setattr(rot, "exec_stdin", fake_exec)
    rot.rotate(NS, SECRET, POD, ["vexa-vexa-admin-api", "vexa-vexa-meeting-api"])
    new = cluster.field("POSTGRES_PASSWORD")
    assert len(new) == 64 and new != "postgres" and cluster.field("POSTGRES_PASSWORD_NEXT") is None
    ((ns, pod, container, command, stdin),) = execs
    assert (ns, pod, container) == (NS, POD, "postgres")
    assert command == ["psql", "-U", "vexa", "-d", "vexa", "-v", "ON_ERROR_STOP=1", "-q"]
    assert new not in " ".join(command)                                  # the password is on stdin only
    assert stdin == f"ALTER ROLE \"vexa\" PASSWORD '{new}';\n\\q\n".encode()
    assert cluster.restarted == ["vexa-vexa-admin-api", "vexa-vexa-meeting-api"]
    assert all(new not in c[1] for c in cluster.calls)                   # never in a URL


def test_a_rotation_begun_before_finishes_with_the_same_value(cluster, monkeypatch):
    cluster.data["POSTGRES_PASSWORD_NEXT"] = _b64("n" * 64)
    seen = []
    monkeypatch.setattr(rot, "exec_stdin", lambda *a, **k: seen.append(a[4]) or 0)
    rot.rotate(NS, SECRET, POD, [])
    assert cluster.field("POSTGRES_PASSWORD") == "n" * 64 and b"n" * 64 in seen[0]


def test_a_database_that_never_takes_it_leaves_next_for_the_next_upgrade(cluster, monkeypatch):
    monkeypatch.setattr(rot, "exec_stdin", lambda *a, **k: 2)
    with pytest.raises(RuntimeError, match="did not take the new password"):
        rot.rotate(NS, SECRET, POD, ["d"], tries=3)
    assert cluster.field("POSTGRES_PASSWORD") == "postgres"
    assert len(cluster.field("POSTGRES_PASSWORD_NEXT")) == 64 and cluster.restarted == []


def test_an_exec_that_fails_to_connect_is_retried(cluster, monkeypatch):
    answers = [ConnectionError("pod starting"), 1, 0]

    def flaky(*a, **k):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(rot, "exec_stdin", flaky)
    rot.rotate(NS, SECRET, POD, [])
    assert answers == [] and cluster.field("POSTGRES_PASSWORD") != "postgres"


def test_names_are_quoted_as_sql_and_the_password_as_a_literal():
    assert rot._sql_ident('we"ird') == '"we""ird"' and rot._sql_literal("a'b") == "'a''b'"


# ── the exec: a WebSocket on the pods/exec subresource ─────────────────────────────────────────

class FakeWs:
    def __init__(self, frames):
        self.frames, self.sent = list(frames), []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send(self, data):
        self.sent.append(data)

    def recv(self, timeout=None):
        return self.frames.pop(0)


@pytest.fixture
def sa(tmp_path, monkeypatch):
    (tmp_path / "token").write_text("tok")
    (tmp_path / "ca.crt").write_text("ca")
    monkeypatch.setattr(k8s_backend, "SA_DIR", str(tmp_path))
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    return tmp_path


def _exec_with(monkeypatch, frames):
    ws, opened = FakeWs(frames), {}

    def connect(url, token, cafile):
        opened.update(url=url, token=token, cafile=cafile)
        return ws
    monkeypatch.setattr(rot, "_connect", connect)
    return ws, opened


def test_exec_sends_stdin_on_channel_0_and_returns_the_status(sa, monkeypatch, capsys):
    ws, opened = _exec_with(monkeypatch, [b"\x01out", b"\x02ERROR:  role \"x\" does not exist\nLINE 1: ALTER ROLE \"x\" PASSWORD 'secret'",
                                          b"\x03" + json.dumps({"status": "Success"}).encode()])
    assert rot.exec_stdin(NS, POD, "postgres", ["psql", "-q"], b"SQL") == 0
    assert ws.sent == [b"\x00SQL"]
    assert opened["url"].startswith(f"wss://10.96.0.1:443/api/v1/namespaces/{NS}/pods/{POD}/exec?container=postgres&stdin=true")
    assert "command=psql&command=-q" in opened["url"] and "SQL" not in opened["url"]
    assert opened["token"] == "tok" and opened["cafile"] == str(sa / "ca.crt")
    err = capsys.readouterr().err
    assert "does not exist" in err and "secret" not in err             # the statement line is never shown


def test_exec_reads_the_exit_code_of_a_failed_command(sa, monkeypatch):
    status = {"status": "Failure", "reason": "NonZeroExitCode",
              "details": {"causes": [{"reason": "ExitCode", "message": "3"}]}}
    _exec_with(monkeypatch, [b"\x03" + json.dumps(status).encode()])
    assert rot.exec_stdin(NS, POD, "postgres", ["psql"], b"") == 3


def test_main_reads_its_env(monkeypatch):
    got = {}
    monkeypatch.setattr(rot, "rotate", lambda ns, secret, pod, consumers: got.update(a=(ns, secret, pod, consumers)))
    for k, v in {"NS": NS, "SECRET": SECRET, "POD": POD, "CONSUMERS": "a b"}.items():
        monkeypatch.setenv(k, v)
    assert rot.main() == 0 and got["a"] == (NS, SECRET, POD, ["a", "b"])
