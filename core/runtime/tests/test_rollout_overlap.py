"""A runtime rollout runs two instances at once: the Deployment's RollingUpdate (maxSurge 1,
maxUnavailable 0) starts the new runtime Pod (B) and keeps the old one (A) serving until B is Ready, so
the Service routes meeting-api's calls to either, and both see the live bot Pods.

Neither instance acts on a Pod on its own (no reaper, no watch): each acts only when a call reaches it,
so what can go wrong is what the two answer and send. Before the fix, B knew only the Pods it adopted
at boot: a bot A spawned after that was a 404 on B (meeting-api's reconcile reads that as "untracked",
and its DELETE could never confirm a teardown once A was gone), and a keep-alive create of it routed to
B made a second record with the callbackUrl, so B re-sent `starting`/`running` and both instances sent
the bot's exit. The kind run of the same scenario: ~/ci/l5-runtime-gate-1116/kind/rollout_overlap.py."""
from __future__ import annotations

from collections import Counter

import pytest

from runtime_kernel import k8s_backend
from runtime_kernel.api import create_app
from runtime_kernel.callbacks import CallbackQueue
from runtime_kernel.kernel import Runtime
from runtime_kernel.profiles import Runnable

from _caller import TOKEN, caller_client
from k8s_fake import FakeApi

CB = "http://meeting-api/callback"


@pytest.fixture
def cluster(monkeypatch):
    return FakeApi().install(monkeypatch)


def _instance(posted: list):
    """One runtime Pod of the release: its own process state, the cluster shared."""
    rt = Runtime(backend=k8s_backend.K8sBackend(), profiles={"meeting-bot": Runnable(image="bot")},
                 grace_sec=0.05)
    queue = CallbackQueue(poster=lambda url, event, headers: posted.append(event) or 200)
    return caller_client(create_app(rt, callback_queue=queue, caller_token=TOKEN))


def _spawn(client, wid):
    return client.post("/workloads", json={"workloadId": wid, "profile": "meeting-bot", "env": {},
                                           "callbackUrl": CB})


def _exit(cluster, wid, code):
    cluster.pods[f"vexa-{wid}"]["status"] = {
        "phase": "Failed" if code else "Succeeded",
        "containerStatuses": [{"state": {"terminated": {"exitCode": code}}}]}


def test_both_instances_serve_and_one_owner_acts_on_each_bot(cluster):
    posted: list = []
    a = _instance(posted)
    assert _spawn(a, "mtg-early").status_code == 201
    b = _instance(posted)                    # the new Pod boots (no Pods to adopt at construction)
    b.app.state.runtime.adopt()              # …and adopts at boot, as build_production_app does
    assert _spawn(a, "mtg-late").status_code == 201        # A still serves after B booted
    uid = {w: cluster.pods[f"vexa-{w}"]["metadata"]["uid"] for w in ("mtg-early", "mtg-late")}

    # B answers for the bot A spawned after B booted: no 404.
    r = b.get("/workloads/mtg-late")
    assert r.status_code == 200 and r.json()["state"] == "running"
    # meeting-api's keep-alive create lands on B: the same Pod, no second record with the callbackUrl.
    before = len(posted)
    r = _spawn(b, "mtg-late")
    assert r.status_code == 201 and r.json()["state"] == "running"
    assert len(posted) == before, "the keep-alive on B re-sent lifecycle callbacks"
    assert cluster.pods["vexa-mtg-late"]["metadata"]["uid"] == uid["mtg-late"]

    # The bot exits; meeting-api's polls reach A, then B.
    _exit(cluster, "mtg-late", 5)
    assert a.get("/workloads/mtg-late").json()["state"] == "stopped"
    assert b.get("/workloads/mtg-late").json()["state"] == "stopped"
    exits = [e for e in posted if e["workloadId"] == "mtg-late" and e["state"] == "stopped"]
    assert [(e["exitCode"], e["stopReason"]) for e in exits] == [(5, "failed")]
    assert Counter((e["workloadId"], e["state"]) for e in posted).most_common(1)[0][1] == 1

    # A goes; B alone accounts for every bot, lists it, and can stop it. No live Pod was touched.
    assert cluster.pods["vexa-mtg-early"]["metadata"]["uid"] == uid["mtg-early"]
    assert not [n for n, _ in cluster.deletes if n == "vexa-mtg-early"]
    ids = {s["workloadId"]: s["state"] for s in b.get("/workloads").json()}
    assert ids == {"mtg-early": "running", "mtg-late": "stopped"}
    assert b.post("/workloads/mtg-early/stop", json={}).json()["state"] == "stopped"
    assert "vexa-mtg-early" not in cluster.pods


def test_b_lists_a_bot_it_never_looked_up(cluster):
    """GET /workloads on B holds a bot A spawned after B booted, before any call named it."""
    a, b = _instance([]), _instance([])
    b.app.state.runtime.adopt()
    _spawn(a, "mtg-late")
    assert [s["workloadId"] for s in b.get("/workloads").json()] == ["mtg-late"]


def test_unknown_id_is_looked_up_by_its_own_label_and_still_404s(cluster):
    """A miss asks the API server for that one workload (one label-selected list per selector,
    not the whole namespace), and an id with no Pod stays a 404."""
    b = _instance([])
    assert b.get("/workloads/mtg-none").status_code == 404
    assert cluster.selectors == [
        "runtime.managed=true,runtime.instance=default,runtime.workload_id=mtg-none",
        "runtime.managed=true,!runtime.instance,runtime.workload_id=mtg-none",
    ]


def test_another_releases_bot_is_never_adopted_on_a_miss(cluster, monkeypatch):
    """Two releases in one namespace: the lookup on a miss selects on this release's instance, so a
    Pod of the other release under the same id stays the other's."""
    monkeypatch.setenv("RUNTIME_K8S_INSTANCE", "release-a")
    _spawn(_instance([]), "mtg-1")
    monkeypatch.setenv("RUNTIME_K8S_INSTANCE", "release-b")
    assert _instance([]).get("/workloads/mtg-1").status_code == 404
