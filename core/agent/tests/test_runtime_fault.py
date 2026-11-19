"""P18 at the runtime edge — every runtime call fails TYPED, and the chat path cannot fail raw.

The 0.13.2 demo stack, the founder's "hi": the runtime refused the worker spawn (kubectl
`AlreadyExists`), `urllib` raised `HTTPError 502`, nothing caught it, and the chat said "Internal
Server Error". He: *"this fails our 'fail loud' principle"*.

Two halves here:
  * the ADAPTER — `RuntimeHttpClient` turns each way a runtime call can fail into a
    `RuntimeFault(source="runtime", kind=…)` whose `detail` names the cause without repeating the
    runtime's own text (it carries the namespace and the pod);
  * the STATIC CHECK (P18's fault-surfacing gate, extended to this path) — the chat dispatch path
    holds no `urlopen` outside the adapter's one translating call, and every runtime call in the
    dispatcher sits under a handler. A new raw call fails here before it fails in front of a person.
"""
from __future__ import annotations

import ast
import io
import json
import pathlib
import socket
import urllib.error
import urllib.request

import pytest

from shared import runtime_fault
from shared.adapters import RuntimeHttpClient
from shared.runtime_fault import RuntimeFault

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: What the k8s backend's StartFailed carried on the demo stack, give or take the names.
KUBECTL_ALREADY_EXISTS = (
    'kubectl create -f - -n vexa-agents failed: Error from server (AlreadyExists): error when '
    'creating "STDIN": pods "agent-u-7f3a-main" already exists')


def _http_error(status: int, body: object = None) -> urllib.error.HTTPError:
    raw = json.dumps(body).encode() if body is not None else b""
    return urllib.error.HTTPError("http://runtime:8090/workloads", status, "err", {}, io.BytesIO(raw))


class _Body:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def runtime(monkeypatch):
    """A real `RuntimeHttpClient` whose one network call raises/returns what the test says."""
    outcome: dict = {}

    def urlopen(req, timeout=None):
        what = outcome["next"]
        if isinstance(what, BaseException):
            raise what
        return _Body(what)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    client = RuntimeHttpClient("http://runtime:8090", token="caller-token")
    client.outcome = outcome
    return client


def _spawn_fault(runtime, what) -> RuntimeFault:
    runtime.outcome["next"] = what
    with pytest.raises(RuntimeFault) as caught:
        runtime.spawn("u-7f3a-main", "agent", {"VEXA_START": "{}"})
    return caught.value


# ── one test per kind ─────────────────────────────────────────────────────────────────────────

def test_the_demo_stack_refusal_is_spawn_refused_and_names_the_cause_without_the_pod(runtime):
    f = _spawn_fault(runtime, _http_error(502, {"detail": KUBECTL_ALREADY_EXISTS}))
    assert (f.source, f.kind, f.op, f.status) == ("runtime", "spawn_refused", "spawn", 502)
    assert f.detail == "a previous agent for this chat is still registered"
    assert f.remedy
    shown = json.dumps(f.as_dict())
    for leak in ("kubectl", "vexa-agents", "agent-u-7f3a-main", "AlreadyExists", "STDIN"):
        assert leak not in shown, leak
    assert "AlreadyExists" in f.upstream        # …the operator's log still gets the runtime's words
    assert f.http_status == 502


def test_a_409_on_spawn_is_the_same_collision(runtime):
    f = _spawn_fault(runtime, _http_error(409, {"detail": "workload exists"}))
    assert f.kind == "spawn_refused"
    assert f.detail == "a previous agent for this chat is still registered"


@pytest.mark.parametrize("upstream, expected", [
    ("docker: No such image: vexaai/agent:0.13.2", "the agent image is not available to the runtime"),
    ('pods "agent-x" is forbidden: exceeded quota: agents', "the cluster has no room for another agent right now"),
    ("something nobody anticipated", "the runtime could not start the agent"),
])
def test_other_refused_spawns_are_named_by_cause(runtime, upstream, expected):
    f = _spawn_fault(runtime, _http_error(502, {"detail": upstream}))
    assert f.kind == "spawn_refused" and f.detail == expected


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_caller_credential_is_unauthorized(runtime, status):
    f = _spawn_fault(runtime, _http_error(status, {"detail": "unauthorized"}))
    assert f.kind == "unauthorized" and f.status == status
    assert "RUNTIME_API_TOKEN" in f.remedy
    assert f.http_status == 502


def test_a_full_quota_is_quota_exceeded_and_a_503(runtime):
    f = _spawn_fault(runtime, _http_error(429, {"detail": "owner 'u-1' at quota cap (3)"}))
    assert f.kind == "quota_exceeded" and f.http_status == 503
    assert "u-1" not in json.dumps(f.as_dict())


def test_a_refused_spec_is_refused(runtime):
    f = _spawn_fault(runtime, _http_error(400, {"detail": "unknown profile: agent"}))
    assert f.kind == "refused" and f.status == 400


@pytest.mark.parametrize("status", [500, 503, 504])
def test_another_server_error_is_unavailable(runtime, status):
    f = _spawn_fault(runtime, _http_error(status))
    assert f.kind == "unavailable" and f.status == status and f.http_status == 503


def test_a_runtime_that_is_not_there_is_unreachable(runtime):
    f = _spawn_fault(runtime, urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")))
    assert (f.kind, f.status, f.http_status) == ("unreachable", None, 503)
    assert f.detail == "the agent runtime could not be reached"


def test_a_runtime_that_does_not_answer_in_time_is_unreachable(runtime):
    f = _spawn_fault(runtime, socket.timeout("timed out"))
    assert f.kind == "unreachable" and f.detail == "the runtime did not answer in time"


def test_a_body_that_is_not_runtime_v1_is_bad_response(runtime):
    f = _spawn_fault(runtime, b"<html>proxy error</html>")
    assert f.kind == "bad_response"


def test_status_and_list_are_translated_too(runtime):
    runtime.outcome["next"] = _http_error(404, {"detail": "unknown workload"})
    with pytest.raises(RuntimeFault) as caught:
        runtime.await_done("u-1")
    assert (caught.value.op, caught.value.kind) == ("status", "not_found")
    runtime.outcome["next"] = urllib.error.URLError(socket.gaierror("Name or service not known"))
    with pytest.raises(RuntimeFault) as caught:
        runtime.live_workloads()
    assert (caught.value.op, caught.value.kind) == ("list", "unreachable")


def test_a_healthy_runtime_still_answers(runtime):
    runtime.outcome["next"] = json.dumps({"workloadId": "u-1", "state": "running"}).encode()
    assert runtime.spawn("u-1", "agent", {}) == "u-1"
    assert runtime.await_done("u-1") == "running"


def test_every_kind_has_one_sentence_and_no_500():
    for kind in runtime_fault.KINDS:
        f = RuntimeFault(kind, op="spawn", detail="d", remedy="r")
        assert f.http_status in (502, 503)
        assert f.sentence().startswith("The agent runtime could not start your agent: d.")
        assert set(f.as_dict()) == {"source", "kind", "op", "status", "detail", "remedy"}


# ── the static check: the chat dispatch path cannot fail raw ───────────────────────────────────

#: The files a chat turn crosses on its way to the runtime.
CHAT_DISPATCH_PATH = ("shared/adapters.py", "control_plane/dispatch.py", "control_plane/routers/chats.py")
#: What a handler must catch for a call under it to count as handled.
_HANDLES = {"Exception", "BaseException", "HTTPError", "URLError", "OSError", "RuntimeFault"}


def _handler_names(node: ast.Try) -> set[str]:
    names: set[str] = set()
    for h in node.handlers:
        if h.type is None:
            names.add("BaseException")
            continue
        for t in (h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]):
            names.add(t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", ""))
    return names


def _unhandled_calls(tree: ast.AST, wanted) -> list[int]:
    """Lines of calls matching ``wanted`` that no enclosing try handles."""
    out: list[int] = []

    def visit(node: ast.AST, handled: bool) -> None:
        if isinstance(node, ast.Try):
            inner = handled or bool(_handler_names(node) & _HANDLES)
            for child in node.body:
                visit(child, inner)
            for child in [*node.handlers, *node.orelse, *node.finalbody]:
                visit(child, handled)
            return
        if isinstance(node, ast.Call) and wanted(node) and not handled:
            out.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child, handled)

    visit(tree, False)
    return out


def _attr_name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def test_the_runtime_adapter_talks_http_in_one_translating_place():
    tree = ast.parse((ROOT / "shared/adapters.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RuntimeHttpClient")
    sites = [(fn.name, c.lineno) for fn in cls.body if isinstance(fn, ast.FunctionDef)
             for c in ast.walk(fn) if isinstance(c, ast.Call) and _attr_name(c) == "urlopen"]
    assert [name for name, _ in sites] == ["_call"], sites
    call = next(fn for fn in cls.body if isinstance(fn, ast.FunctionDef) and fn.name == "_call")
    assert not _unhandled_calls(call, lambda c: _attr_name(c) == "urlopen")


@pytest.mark.parametrize("rel", CHAT_DISPATCH_PATH[1:])
def test_the_chat_dispatch_path_holds_no_raw_http_call(rel):
    """dispatch.py and the chat router reach the runtime only through the port — never urllib."""
    tree = ast.parse((ROOT / rel).read_text())
    raw = _unhandled_calls(tree, lambda c: _attr_name(c) in ("urlopen", "Request"))
    assert not raw, f"{rel}: an HTTP call outside the runtime adapter at lines {raw}"


def test_every_runtime_call_in_the_dispatcher_is_handled():
    """A `spawn` or `await_done` outside a try is a RuntimeFault the dispatcher did not decide about
    — exactly the shape of the 500 this file exists for."""
    tree = ast.parse((ROOT / "control_plane/dispatch.py").read_text())
    is_runtime_call = lambda c: (isinstance(c.func, ast.Attribute) and c.func.attr in ("spawn", "await_done")
                                 and isinstance(c.func.value, ast.Attribute)
                                 and c.func.value.attr == "_runtime")
    calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and is_runtime_call(c)]
    assert len(calls) >= 3      # the spawn, the watchdog respawn, the liveness probe
    assert not _unhandled_calls(tree, is_runtime_call)


def test_the_chat_route_answers_a_runtime_fault_rather_than_letting_it_climb():
    src = (ROOT / "control_plane/routers/chats.py").read_text()
    assert "except RuntimeFault as fault" in src
    api = (ROOT / "control_plane/api.py").read_text()
    assert "@app.exception_handler(RuntimeFault)" in api


# ── S67: a list answer that is not a list of workloads is an error, never "nothing is live" ─────

@pytest.mark.parametrize("body", [
    {"workloads": [{"workloadId": "u-1", "state": "running"}]},   # an envelope, not the list
    {"detail": "ok"},
    "running",
    None,
    [{"workloadId": "u-1", "state": "running"}, "u-2"],             # one row that is not a workload
    [{"id": "u-1", "state": "running"}],
    [{"workloadId": "u-1"}],
])
def test_a_list_answer_that_is_not_workloads_is_bad_response_not_empty(runtime, body):
    runtime.outcome["next"] = json.dumps(body).encode()
    with pytest.raises(RuntimeFault) as caught:
        runtime.live_workloads()
    assert (caught.value.op, caught.value.kind) == ("list", "bad_response")


def test_a_real_list_still_answers_the_live_set(runtime):
    runtime.outcome["next"] = json.dumps([
        {"workloadId": "u-1", "state": "running"}, {"workloadId": "u-2", "state": "starting"},
        {"workloadId": "u-3", "state": "stopped"}]).encode()
    assert runtime.live_workloads() == ["u-1", "u-2"]
    runtime.outcome["next"] = b"[]"
    assert runtime.live_workloads() == []


def test_neither_sweeper_acts_on_an_unreadable_live_set(runtime):
    """The two unit-end sweepers act on what is ABSENT from the live set: read as empty, an
    unreadable answer would revoke every token and delete every worker's Redis user."""
    import fakeredis

    from control_plane import delegation_revocation as dr

    runtime.outcome["next"] = json.dumps({"workloads": []}).encode()
    store = fakeredis.FakeRedis(decode_responses=True)
    t = 10_000_000.0
    dr.record(store, unit_id="u-live", jti="j-live", exp=int(t) + 900, now=t - 600)
    with pytest.raises(RuntimeFault):
        dr.sweep(store, runtime.live_workloads, now=t)
    assert not store.exists(dr.revoked_key("j-live"))
    assert store.hexists(dr.unit_key("u-live"), "j-live")
