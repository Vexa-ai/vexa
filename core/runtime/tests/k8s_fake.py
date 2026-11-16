"""An in-memory stand-in for the four Kubernetes API calls the K8sBackend makes (create a Pod or a
Secret, get a Pod, list Pods by label, delete a Pod), installed in place of ``k8s_backend._api``.

It answers the way the API server does — 201 with the stored object (a uid added), 409
AlreadyExists, 200 or 404 Status objects — and records every call, so a test asserts both what
the backend sent and what it did with the answer."""
from __future__ import annotations

import copy
import re
from typing import Optional

from runtime_kernel import k8s_backend

_PATH = re.compile(r"^/api/v1/namespaces/(?P<ns>[^/]+)/(?P<res>pods|secrets)(?:/(?P<name>[^/]+))?$")


def _status(code: int, reason: str, message: str) -> dict:
    return {"kind": "Status", "apiVersion": "v1", "status": "Failure", "code": code, "reason": reason, "message": message}


def _match(labels: dict, selector: str) -> bool:
    for term in filter(None, selector.split(",")):
        if term.startswith("!"):
            if term[1:] in labels:
                return False
        else:
            k, _, v = term.partition("=")
            if labels.get(k) != v:
                return False
    return True


class FakeApi:
    def __init__(self, list_answers: Optional[dict] = None, fail: Optional[dict] = None,
                 uid: Optional[str] = None) -> None:
        self.pods: dict[str, dict] = {}
        self.secrets: list[dict] = []
        self.calls: list[tuple[str, str, Optional[dict], Optional[dict]]] = []
        self.namespaces: list[str] = []
        #: labelSelector → items, answered as given (instead of filtering the stored Pods).
        self.list_answers = list_answers
        #: (method, resource) → (status, reason): that call fails with that Status.
        self.fail = fail or {}
        #: the uid every created Pod gets (default: uid-<name>)
        self.uid = uid

    # what the backend sent
    @property
    def submitted(self) -> list[dict]:
        return [c[3] for c in self.calls if c[0] == "POST" and c[3] and c[3].get("kind", "Pod") == "Pod"]

    @property
    def selectors(self) -> list[str]:
        return [c[2]["labelSelector"] for c in self.calls if c[0] == "GET" and c[2] and "labelSelector" in c[2]]

    @property
    def deletes(self) -> list[tuple[str, Optional[dict]]]:
        return [(c[1].rsplit("/", 1)[1], c[3]) for c in self.calls if c[0] == "DELETE"]

    def install(self, monkeypatch) -> "FakeApi":
        monkeypatch.setattr(k8s_backend, "_api", self)
        monkeypatch.setattr(k8s_backend, "_sa_namespace", lambda: "sa-ns")
        return self

    def __call__(self, method: str, path: str, *, body: Optional[dict] = None,
                 params: Optional[dict] = None) -> tuple[int, dict]:
        self.calls.append((method, path, copy.deepcopy(params), copy.deepcopy(body)))
        m = _PATH.match(path)
        assert m, f"not a core v1 namespaced pods/secrets path: {path}"
        ns, res, name = m["ns"], m["res"], m["name"]
        self.namespaces.append(ns)
        if (method, res) in self.fail:
            code, reason = self.fail[(method, res)]
            return code, _status(code, reason, f"{res} {reason.lower()}")
        if method == "POST" and name is None:
            obj = copy.deepcopy(body)
            meta = obj.setdefault("metadata", {})
            if res == "secrets":
                self.secrets.append(obj)
                return 201, obj
            if meta["name"] in self.pods:
                return 409, _status(409, "AlreadyExists", f'pods "{meta["name"]}" already exists')
            meta["uid"] = self.uid or f"uid-{meta['name']}"
            obj.setdefault("status", {"phase": "Pending"})
            self.pods[meta["name"]] = obj
            return 201, copy.deepcopy(obj)
        if method == "GET" and res == "pods" and name is not None:
            if name in self.pods:
                return 200, copy.deepcopy(self.pods[name])
            return 404, _status(404, "NotFound", f'pods "{name}" not found')
        if method == "GET" and res == "pods":
            selector = (params or {}).get("labelSelector", "")
            if self.list_answers is not None:
                items = self.list_answers.get(selector, [])
            else:
                items = [p for p in self.pods.values() if _match((p.get("metadata") or {}).get("labels") or {}, selector)]
            return 200, {"kind": "PodList", "items": copy.deepcopy(items)}
        if method == "DELETE" and res == "pods" and name is not None:
            if self.pods.pop(name, None) is None:
                return 404, _status(404, "NotFound", f'pods "{name}" not found')
            return 200, {"kind": "Pod", "metadata": {"name": name}}
        raise AssertionError(f"the K8sBackend makes no {method} {path}")
