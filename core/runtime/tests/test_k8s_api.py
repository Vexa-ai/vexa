"""The K8sBackend's four API calls, without kubectl: the HTTPS request each one makes (path, method,
body, query, the ServiceAccount's token and CA) and what it does with each answer. The transport
(``requests``) is faked; tests/k8s_fake.py fakes the API server one level up for the lifecycle tests."""
from __future__ import annotations

import logging

import pytest
import requests
from k8s_fake import FakeApi

from runtime_kernel import k8s_backend
from runtime_kernel.k8s_backend import ApiError, K8sBackend
from runtime_kernel.profiles import Runnable


class _Resp:
    def __init__(self, status: int, body=None, text_only: bool = False):
        self.status_code, self._body, self._text_only = status, body, text_only

    def json(self):
        if self._text_only:
            raise ValueError("not JSON")
        return self._body


@pytest.fixture
def sa(tmp_path, monkeypatch):
    """A mounted ServiceAccount (token, CA, namespace) and the kubernetes Service env of a Pod."""
    (tmp_path / "token").write_text("tok-1\n")
    (tmp_path / "ca.crt").write_text("-----BEGIN CERTIFICATE-----\n")
    (tmp_path / "namespace").write_text("vexa-ns\n")
    monkeypatch.setattr(k8s_backend, "SA_DIR", str(tmp_path))
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    return tmp_path


@pytest.fixture
def wire(monkeypatch):
    """Capture every HTTPS request; answer from a queue (default 200 {})."""
    sent, answers = [], []

    def request(method, url, **kw):
        sent.append({"method": method, "url": url, **kw})
        a = answers.pop(0) if answers else _Resp(200, {})
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(requests, "request", request)
    return sent, answers


# ── the transport: one HTTPS call under the ServiceAccount ──────────────────────────────────────

def test_a_call_goes_to_the_api_server_with_the_service_accounts_token_and_ca(sa, wire):
    sent, answers = wire
    answers.append(_Resp(200, {"kind": "Pod"}))
    assert k8s_backend._api("GET", "/api/v1/namespaces/n/pods/p", params={"labelSelector": "a=b"}) == (200, {"kind": "Pod"})
    (r,) = sent
    assert r["method"] == "GET" and r["url"] == "https://10.96.0.1:443/api/v1/namespaces/n/pods/p"
    assert r["headers"]["Authorization"] == "Bearer tok-1"
    assert r["verify"] == str(sa / "ca.crt")               # the cluster CA, never verify=False
    assert r["timeout"] == k8s_backend.API_TIMEOUT_S and r["params"] == {"labelSelector": "a=b"}


def test_the_token_is_read_for_every_call(sa, wire):
    """The kubelet rotates a projected token: a call never reuses one it read before."""
    sent, _ = wire
    k8s_backend._api("GET", "/x")
    (sa / "token").write_text("tok-2")
    k8s_backend._api("GET", "/x")
    assert [r["headers"]["Authorization"] for r in sent] == ["Bearer tok-1", "Bearer tok-2"]


def test_an_ipv6_service_host_is_bracketed(sa, wire, monkeypatch):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "fd00::1")
    k8s_backend._api("GET", "/x")
    assert wire[0][0]["url"] == "https://[fd00::1]:443/x"


def test_outside_a_cluster_the_call_refuses_by_name(sa, wire, monkeypatch):
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST")
    with pytest.raises(RuntimeError, match="KUBERNETES_SERVICE_HOST is unset"):
        k8s_backend._api("GET", "/x")
    assert wire[0] == []


def test_a_call_with_no_answer_raises_and_a_body_that_is_not_json_is_empty(sa, wire):
    _, answers = wire
    answers.append(requests.ConnectionError("refused"))
    with pytest.raises(RuntimeError, match="k8s API GET /x: refused"):
        k8s_backend._api("GET", "/x")
    answers.append(_Resp(503, text_only=True))
    assert k8s_backend._api("GET", "/x") == (503, {})


def test_the_namespace_is_the_given_one_else_the_pods_own(sa, wire):
    K8sBackend(namespace="given")._pod("p")
    K8sBackend()._pod("p")
    assert [r["url"].split("/pods/")[0].rsplit("/", 1)[1] for r in wire[0]] == ["given", "vexa-ns"]


# ── verb create: a Pod, and its env Secret owned by it ──────────────────────────────────────────

def test_create_posts_the_pod_then_its_secret_owned_by_the_pods_uid(monkeypatch):
    api = FakeApi(uid="u-1").install(monkeypatch)
    h = K8sBackend(namespace="ns").start("mtg-1", Runnable(image="img"), {"SOME_TOKEN": "t"})
    (pod_call, secret_call) = [c for c in api.calls if c[0] == "POST"]
    assert pod_call[1] == "/api/v1/namespaces/ns/pods" and pod_call[3]["metadata"]["name"] == h._impl
    assert secret_call[1] == "/api/v1/namespaces/ns/secrets"
    (secret,) = api.secrets
    assert secret["stringData"] == {"SOME_TOKEN": "t"}
    assert secret["metadata"]["ownerReferences"] == [{"apiVersion": "v1", "kind": "Pod", "name": h._impl, "uid": "u-1"}]


def test_create_with_no_secret_env_posts_only_the_pod(monkeypatch):
    api = FakeApi().install(monkeypatch)
    K8sBackend(namespace="ns").start("mtg-1", Runnable(image="img"), {})
    assert [(c[0], c[1]) for c in api.calls] == [("POST", "/api/v1/namespaces/ns/pods")] and not api.secrets


def test_create_refused_raises_the_api_servers_reason(monkeypatch):
    FakeApi(fail={("POST", "pods"): (403, "Forbidden")}).install(monkeypatch)
    with pytest.raises(ApiError, match="403 Forbidden") as e:
        K8sBackend(namespace="ns").start("mtg-1", Runnable(image="img"), {})
    assert e.value.status == 403 and e.value.reason == "Forbidden"


def test_create_on_a_taken_name_is_answered_409_and_reads_the_pod(monkeypatch):
    api = FakeApi().install(monkeypatch)
    be = K8sBackend(namespace="ns", instance="rel")
    first = be.start("mtg-1", Runnable(image="img"), {})
    api.pods[first._impl]["status"] = {"phase": "Running"}
    again = be.start("mtg-1", Runnable(image="img"), {})
    assert again._impl == first._impl and len(api.submitted) == 2 and len(api.pods) == 1
    assert [c[0] for c in api.calls] == ["POST", "POST", "GET"]


# ── verb get: one Pod ───────────────────────────────────────────────────────────────────────────

def test_get_answers_the_pod_and_none_when_it_is_not_there(monkeypatch):
    api = FakeApi().install(monkeypatch)
    be = K8sBackend(namespace="ns")
    h = be.start("mtg-1", Runnable(image="img"), {})
    assert be._pod(h._impl)["metadata"]["name"] == h._impl
    assert be.find("mtg-1")._impl == h._impl
    assert be._pod("nope") is None and be.find("nope") is None
    assert ("GET", f"/api/v1/namespaces/ns/pods/{h._impl}") in [c[:2] for c in api.calls]


def test_get_with_no_answer_is_none_and_logged(monkeypatch, caplog):
    def broken(method, path, **kw):
        raise RuntimeError("k8s API GET: timed out")
    monkeypatch.setattr(k8s_backend, "_api", broken)
    with caplog.at_level(logging.WARNING, logger="runtime_kernel.k8s"):
        assert K8sBackend(namespace="ns")._pod("p") is None
    assert "timed out" in caplog.text


# ── verb list: Pods by label ────────────────────────────────────────────────────────────────────

def test_list_asks_by_label_selector_and_reads_the_items(monkeypatch):
    api = FakeApi().install(monkeypatch)
    be = K8sBackend(namespace="ns", instance="rel")
    be.start("mtg-1", Runnable(image="img"), {})
    found = be.list_workload_containers()
    assert [w["workload_id"] for w in found] == ["mtg-1"] and found[0]["running"] is True
    lists = [c for c in api.calls if c[0] == "GET" and c[2]]
    assert [c[1] for c in lists] == ["/api/v1/namespaces/ns/pods"] * 2
    assert api.selectors == ["runtime.managed=true,runtime.instance=rel", "runtime.managed=true,!runtime.instance"]


def test_list_refused_is_empty_never_a_crash(monkeypatch):
    FakeApi(fail={("GET", "pods"): (403, "Forbidden")}).install(monkeypatch)
    assert K8sBackend(namespace="ns").list_workload_containers() == []


# ── verb delete: graceful, forced, after an exit ────────────────────────────────────────────────

def test_terminate_deletes_with_the_stop_grace_and_kill_and_cleanup_with_zero(monkeypatch):
    api = FakeApi().install(monkeypatch)
    monkeypatch.setattr(k8s_backend, "_stop_grace_sec", lambda: 25)
    be = K8sBackend(namespace="ns")
    for verb in ("terminate", "kill", "cleanup"):
        h = be.start(f"w-{verb}", Runnable(image="img"), {})
        getattr(be, verb)(h)
        assert h._impl not in api.pods
    grace = [(body or {}).get("gracePeriodSeconds") for _, body in api.deletes]
    assert grace == [25, 0, 0]
    assert all(body["kind"] == "DeleteOptions" for _, body in api.deletes)


def test_an_observed_exit_deletes_the_pod_with_its_own_grace(monkeypatch):
    api = FakeApi().install(monkeypatch)
    be = K8sBackend(namespace="ns")
    h = be.start("mtg-1", Runnable(image="img"), {})
    api.pods[h._impl]["status"] = {"phase": "Succeeded"}
    assert be.exit_code(h) == 0
    assert api.deletes == [(h._impl, None)] and not api.pods


def test_delete_of_a_gone_pod_is_quiet_and_a_refused_one_is_logged_not_raised(monkeypatch, caplog):
    FakeApi().install(monkeypatch)
    be = K8sBackend(namespace="ns")
    with caplog.at_level(logging.WARNING, logger="runtime_kernel.k8s"):
        be._delete_pod("gone")
    assert caplog.text == ""
    FakeApi(fail={("DELETE", "pods"): (500, "InternalError")}).install(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="runtime_kernel.k8s"):
        be._delete_pod("p", grace_sec=0)
    assert "500 InternalError" in caplog.text


def test_a_secret_refused_after_its_pod_deletes_the_pod(monkeypatch):
    api = FakeApi(fail={("POST", "secrets"): (403, "Forbidden")}).install(monkeypatch)
    with pytest.raises(ApiError):
        K8sBackend(namespace="ns").start("mtg-1", Runnable(image="img"), {"SOME_TOKEN": "t"})
    assert not api.pods and len(api.deletes) == 1
