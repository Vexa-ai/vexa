"""The low findings of security pass 6 (and the carried R-5, R-7) in agent-api and the worker.

Each test fails on the code before its fix:

* R6-23 — the deployment route's model probe never follows a redirect either (a person's endpoint
  is held to it by ed1f66307);
* R6-26 — the chat-continuity link is made through the HOME/.claude descriptor that was checked;
* R6-28 — share-enable takes only a slot the store records;
* R6-29 — un-share never takes a desk;
* R-5 — claim batches are bounded and two writers never lose each other's claims;
* R-7 — accepting the company layer needs a person, and the commit names the caller only.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

# ── R6-23 ─────────────────────────────────────────────────────────────────────────────────────────

def _server(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_the_deployment_model_probe_never_follows_a_redirect(monkeypatch):
    from control_plane import config_test

    seen: list = []

    class Elsewhere(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        do_POST = do_GET

        def log_message(self, *a):
            pass

    other = _server(Elsewhere)

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{other.server_port}/steal")
            self.end_headers()

        def log_message(self, *a):
            pass

    first = _server(Redirect)
    monkeypatch.setenv("VEXA_MODEL_BASE_URL_ALLOW", "127.0.0.1")
    out = config_test.test_custom_endpoint(f"http://127.0.0.1:{first.server_port}", "sk-test-key", "m")
    first.shutdown(); other.shutdown()
    assert seen == []                                    # the key never reached the redirect target
    assert out["ok"] is False


# ── R6-26 ─────────────────────────────────────────────────────────────────────────────────────────

def test_the_chat_link_is_made_through_the_checked_home_folder(monkeypatch, tmp_path):
    from llm import claude_code

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    real = claude_code.wpaths.dir_fd_inside
    swapped = {"done": False}

    def checked_then_swapped(root, parts, *a, **kw):
        fd = real(root, parts, *a, **kw)
        if not swapped["done"] and Path(root) == home and tuple(parts) == (".claude",):
            swapped["done"] = True
            os.rename(home / ".claude", home / ".claude-real")
            os.symlink(outside, home / ".claude")
        return fd

    monkeypatch.setattr(claude_code.wpaths, "dir_fd_inside", checked_then_swapped)
    claude_code._link_chat_into_workspace(work)
    assert swapped["done"]
    assert not os.path.lexists(outside / "projects")     # nothing made through the swapped link


# ── R6-28, R6-29 ──────────────────────────────────────────────────────────────────────────────────

def test_share_enable_takes_only_a_recorded_slot(tmp_path):
    from control_plane import workspace_attach as wa

    root = tmp_path.resolve()
    (root / "u1").mkdir()
    stray = root / wa.STORE_DIRNAME / "u1" / "stray"
    stray.mkdir(parents=True)
    (stray / "x.md").write_text("not a workspace of theirs")
    with pytest.raises(KeyError):
        wa.ensure_workspace_shareable(root, "u1", "stray")
    assert stray.is_dir()                                # never re-homed to the top level


def test_unshare_never_takes_a_desk(tmp_path):
    from control_plane import workspace_attach as wa

    root = tmp_path.resolve()
    desk = root / "u2"
    (desk / "policy").mkdir(parents=True)
    (desk / "policy" / "members.json").write_text(json.dumps([{"subject": "u1", "role": "owner"}]))
    (root / ".system" / "u2").mkdir(parents=True)        # u2 is a person: this is their desk
    (root / "u1").mkdir()
    with pytest.raises(KeyError):
        wa.ensure_workspace_private(root, "u1", "u2")
    assert desk.is_dir()
    with pytest.raises(KeyError):
        wa.ensure_workspace_private(root, "u1", "u1")


# ── R-5 ───────────────────────────────────────────────────────────────────────────────────────────

def test_claim_batches_are_bounded(tmp_path):
    from pydantic import ValidationError

    from control_plane.bodies import ClaimsProposeBody, ClaimVerdictsBody
    with pytest.raises(ValidationError):
        ClaimsProposeBody(claims=["x"] * 51)
    with pytest.raises(ValidationError):
        ClaimVerdictsBody(verdicts=[{"id": "c001", "verdict": "confirmed"}] * 51)


def test_two_proposers_never_lose_each_others_claims(monkeypatch, tmp_path):
    from control_plane import claims

    desk = tmp_path / "desk"
    desk.mkdir()
    real_load = claims._load

    def slow_load(ws):
        book = real_load(ws)
        time.sleep(0.3)                                  # both read before either writes
        return book

    monkeypatch.setattr(claims, "_load", slow_load)
    threads = [threading.Thread(target=claims.propose, args=(desk, [f"claim {i}"])) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    book = json.loads((desk / claims.CLAIMS_PATH).read_text())
    assert sorted(c["claim"] for c in book["claims"]) == ["claim 1", "claim 2"]


# ── R-7 ───────────────────────────────────────────────────────────────────────────────────────────

def test_accepting_the_company_layer_needs_a_person():
    from control_plane import route_policy
    assert ("POST", "/api/global/ready") in route_policy.PERSON_VERBS


def test_the_company_layer_commit_names_the_caller_not_the_body(tmp_path):
    from control_plane import global_layer
    from tests.test_membership_at_the_edge import ADMIN, _client

    g = tmp_path / "_global"
    g.mkdir()
    global_layer.ensure_repo(g)
    for name in global_layer.LAYER_FILES:
        (g / name).write_text("# x\n\nsomething.\n")
    (g / "README.md").write_text("# Acme GmbH\n\nAcme GmbH sells widgets.\n")
    r = _client(tmp_path).post("/api/global/ready",
                               json={"author_email": "someone-else@acme.example"},
                               headers={"X-User-Id": ADMIN, "x-user-email": "ada@acme.example"})
    assert r.status_code == 200, r.text
    author = subprocess.run(["git", "-C", str(g), "log", "-1", "--format=%ae"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert author == "ada@acme.example"
