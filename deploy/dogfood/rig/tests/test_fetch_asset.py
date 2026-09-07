"""`fetch_asset` — a picture reaches a page by being brought INTO the workspace (Vexa-ai/vexa#1612).

Founder, 2026-09-06: the agent had written `![OeNB logo](https://…)` onto a customer's README and
the page showed a broken image. Both halves of that are the defect. The remote URL is the one that
matters: a document in a customer's workspace that links a picture off a third party sends THEIR
browser to a stranger every time anyone opens it, keeps working only as long as that site does, and
is a request we cannot see.

So this tool is judged on three things, and every one of them is asserted here:

* it goes through **agent-api's own asset route on the caller's identity**, not around it — the same
  fix `workspace_write` took in F96, for the same reason: the service that owns the resource is the
  only place the membership rules and the outbound guard exist;
* a **path it is given is validated** by the shipped workspace-path validator, so a name that
  climbs out of the workspace is refused here rather than negotiated with the server;
* a **refusal is told plainly and never worked around** — the whole failure this replaces is an
  agent that, unable to bring a picture in, writes the remote URL onto the page instead.
"""
from __future__ import annotations

import json

from conftest import as_user, tool


def _fetch(monkeypatch, routes=None, **kw):
    http = as_user(monkeypatch, uid="7", routes=routes)
    out = json.loads(tool("fetch_asset")(**kw))
    return http, out


STORED = {"path": "assets/oenb-logo.svg", "bytes": 1234, "source": "https://oenb.at/logo.svg",
          "content_type": "image/svg+xml"}


def test_it_asks_agent_api_on_the_callers_identity(monkeypatch):
    http, out = _fetch(monkeypatch, routes={"/api/workspace/asset": (200, STORED)},
                       url="https://oenb.at/logo.svg")
    call = next(c for c in http.calls if "/api/workspace/asset" in c["url"])
    assert call["method"] == "POST"
    assert call["headers"]["X-User-Id"] == "7"
    assert call["body"] == {"url": "https://oenb.at/logo.svg"}
    assert out["stored"] == "assets/oenb-logo.svg"


def test_it_hands_back_the_markdown_that_puts_it_on_the_page(monkeypatch):
    _http, out = _fetch(monkeypatch, routes={"/api/workspace/asset": (200, STORED)},
                        url="https://oenb.at/logo.svg")
    # a RELATIVE reference — the thing the agent is meant to paste, so it cannot compose a remote
    # one by hand and call it the same
    assert out["put_this_on_the_page"] == "![oenb-logo.svg](assets/oenb-logo.svg)"
    assert "https://" not in out["put_this_on_the_page"]
    assert out["source"] == "https://oenb.at/logo.svg"


def test_a_named_path_and_a_workspace_travel_with_the_request(monkeypatch):
    http, _out = _fetch(monkeypatch, routes={"/api/workspace/asset": (200, STORED)},
                        url="https://oenb.at/logo.svg", path="assets/oenb-logo.svg",
                        slug="vexa-team-3183d1")
    body = next(c for c in http.calls if "/api/workspace/asset" in c["url"])["body"]
    assert body == {"url": "https://oenb.at/logo.svg", "path": "assets/oenb-logo.svg",
                    "slug": "vexa-team-3183d1"}


def test_a_path_that_climbs_out_of_the_workspace_is_refused_before_the_request(monkeypatch):
    http, out = _fetch(monkeypatch, url="https://oenb.at/logo.svg", path="../../etc/logo.svg")
    assert out["refused"] == "invalid_path"
    assert http.urls("/api/workspace/asset") == []


def test_a_refusal_says_so_and_forbids_the_workaround_it_exists_to_prevent(monkeypatch):
    _http, out = _fetch(
        monkeypatch,
        routes={"/api/workspace/asset": (400, {"detail": "refusing '169.254.169.254'"})},
        url="http://169.254.169.254/latest/meta-data/")
    assert out["refused"] == "not_fetched" and out["status"] == 400
    assert "169.254.169.254" in json.dumps(out["why"])
    # the one instruction that has to be in the answer: do NOT put the remote URL on the page
    assert "never_hotlink" in out
