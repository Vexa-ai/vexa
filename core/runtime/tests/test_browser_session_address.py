"""A sign-in browser session Pod is addressable in cluster DNS; no other workload is.

meeting-api probes a browser session's noVNC on 6080 at ``vexa-bs-<id>.vexa-bs.<ns>.svc.cluster.local``.
A bare Pod name resolves to nothing, so the k8s backend gives a ``bs-`` workload's Pod
``hostname`` = its name and ``subdomain`` = the chart's headless Service, plus the label that Service
selects on. Meeting bots, checks (``bc-``) and agent workers get none of it.
"""
from runtime_kernel import default_registry
from runtime_kernel.k8s_backend import (DEFAULT_SUBDOMAIN, SUBDOMAIN_ENV, SUBDOMAIN_LABEL,
                                        browser_session_subdomain, build_pod, k8s_name)


def _pod(workload_id: str, profile: str = "meeting-bot") -> dict:
    return build_pod(name=k8s_name(f"vexa-{workload_id}"), workload_id=workload_id,
                     runnable=default_registry().get(profile).runnable, env={}, namespace="ns",
                     resources=None)


def test_a_browser_session_pod_gets_its_name_as_hostname_under_the_headless_service():
    wid = "bs-" + "0123456789abcdef" * 2          # meeting-api: f"bs-{secrets.token_hex(16)}"
    pod = _pod(wid)
    assert pod["metadata"]["name"] == f"vexa-{wid}"   # = meeting-api's default host template
    assert pod["spec"]["hostname"] == f"vexa-{wid}"
    assert pod["spec"]["subdomain"] == DEFAULT_SUBDOMAIN == "vexa-bs"
    assert pod["metadata"]["labels"][SUBDOMAIN_LABEL] == "vexa-bs"


def test_other_workloads_get_no_address():
    for wid, profile in (("bc-0123", "meeting-bot"), ("meeting-bot-1234", "meeting-bot"),
                         ("agent-58-chat", "agent"), ("xbs-1", "meeting-bot")):
        pod = _pod(wid, profile)
        assert "hostname" not in pod["spec"] and "subdomain" not in pod["spec"], wid
        assert SUBDOMAIN_LABEL not in pod["metadata"]["labels"], wid


def test_the_subdomain_is_operator_configuration():
    assert browser_session_subdomain("bs-1", {SUBDOMAIN_ENV: "bs-svc"}) == "bs-svc"
    assert browser_session_subdomain("bs-1", {}) == "vexa-bs"
    assert browser_session_subdomain("bs-1", {SUBDOMAIN_ENV: "  "}) == "vexa-bs"
    assert browser_session_subdomain("bc-1", {SUBDOMAIN_ENV: "bs-svc"}) is None


def test_hostname_is_a_dns_label_even_when_the_id_is_not():
    pod = _pod("bs-Some_Odd.ID")
    host = pod["spec"]["hostname"]
    assert host == pod["metadata"]["name"] and len(host) <= 63 and host == host.lower()
