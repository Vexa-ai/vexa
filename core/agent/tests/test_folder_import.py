"""A dropped folder becomes a workspace (`/api/workspace/folder/*`, control_plane/folder_import.py).

Pinned from the server side, where every rule is enforced: the limits (total, count, per file) read
from `_global/POLICIES.md`; the skip rules (.git internals, dependency folders, system litter, the
folder's own .gitignore); `.env*` and credentials refused by name AND by content; ONE commit authored
by the person; and access — the subject is the gateway's, a plan is its owner's alone, and an import
INTO a shared workspace needs a contributor.
"""
from __future__ import annotations

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from control_plane import folder_import as fi
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests.test_api import _FakeIdentity, _FakeRuntime

H = {"X-User-Id": "u_jane", "X-User-Email": "jane@example.org"}
KB = 1024
MB = 1024 * 1024


@pytest.fixture
def env(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "CLAUDE.md").write_text("SEED\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    ws = tmp_path / "ws"
    c = TestClient(create_app(Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
                              reader=WorkspaceReader(str(ws))))
    c.post("/api/workspace/init", headers=H)
    return c, ws


def _policy(ws, **keys):
    from control_plane import system_mounts
    g = system_mounts.global_root(load_settings(), str(ws))   # where the app reads `_global`
    g.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{k}: {v}" for k, v in keys.items())
    (g / "POLICIES.md").write_text(f"---\nkind: policies\n{body}\n---\n\n# Policies\n")


def _manifest(files, **extra):
    return {"name": "Research notes", "files": [{"path": p, "size": len(b)} for p, b in files.items()],
            **extra}


def _upload(c, plan_id, files, headers=H):
    multipart = [("files", (p.rsplit("/", 1)[-1], b, "application/octet-stream")) for p, b in files.items()]
    data = {"paths": list(files)}
    return c.post(f"/api/workspace/folder/{plan_id}/files", headers=headers, data=data, files=multipart)


def _import(c, files, *, manifest_extra=None, finish=None, headers=H):
    plan = c.post("/api/workspace/folder/plan", headers=headers, json=_manifest(files, **(manifest_extra or {})))
    assert plan.status_code == 200, plan.text
    body = plan.json()
    accepted = {a["path"] for a in body["accepted"]}
    up = _upload(c, body["plan_id"], {p: b for p, b in files.items() if p in accepted}, headers)
    assert up.status_code == 200, up.text
    done = c.post(f"/api/workspace/folder/{body['plan_id']}/finish", headers=headers, json=finish or {})
    return body, up.json(), done


def _git(path, *args):
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout


# ── the rules, as a unit ─────────────────────────────────────────────────────────────────────────

def test_skip_rules_cover_git_dependencies_system_files_and_secrets():
    assert fi.skip_reason(".git/config") == fi.REASON_GIT
    assert fi.skip_reason("web/node_modules/x/index.js") == fi.REASON_DEPS
    assert fi.skip_reason("api/.venv/bin/python") == fi.REASON_DEPS
    assert fi.skip_reason("pkg/__pycache__/m.cpython-312.pyc") == fi.REASON_DEPS
    assert fi.skip_reason("notes/.DS_Store") == fi.REASON_SYSTEM
    assert fi.skip_reason(".env") == fi.REASON_ENV
    assert fi.skip_reason("deploy/.env.production") == fi.REASON_ENV
    assert fi.skip_reason("keys/id_ed25519") == fi.REASON_SECRET_NAME
    assert fi.skip_reason("certs/server.pem") == fi.REASON_SECRET_NAME
    assert fi.skip_reason("../escape.md") == fi.REASON_PATH
    assert fi.skip_reason("docs/readme.md") is None


def test_gitignore_follows_gits_rules_nested_files_negation_and_anchoring():
    ig = fi.Gitignore({
        ".gitignore": "*.log\n/build/\ndist\n!keep.log\n",
        "app/.gitignore": "secret-*.txt\n/local.md\n",
    })
    assert ig.ignored("debug.log") and ig.ignored("a/b/trace.log")
    assert not ig.ignored("keep.log")
    assert ig.ignored("build/out.bin") and not ig.ignored("src/build/notes.md")
    assert ig.ignored("dist/app.js") and ig.ignored("pkg/dist/app.js")
    assert ig.ignored("app/secret-1.txt") and ig.ignored("app/deep/secret-2.txt")
    assert ig.ignored("app/local.md") and not ig.ignored("app/sub/local.md")
    assert not ig.ignored("secret-1.txt")          # app/.gitignore applies under app/ only
    ig2 = fi.Gitignore({".gitignore": "data/**\n!data/README.md\n"})
    assert ig2.ignored("data/x.csv") and not ig2.ignored("data/README.md")


def test_policy_keys_default_and_refuse_loudly():
    assert fi.resolve(None) == fi.DEFAULTS
    assert fi.resolve("---\nfolder_import_max_mb: 50\n---\n")["folder_import_max_mb"] == 50
    with pytest.raises(fi.PolicyUnreadable) as e:
        fi.resolve("---\nfolder_import_max_mb: lots\n---\n")
    assert e.value.key == "folder_import_max_mb"
    with pytest.raises(fi.PolicyUnreadable):
        fi.resolve("---\nfolder_import_max_bytes: 5\n---\n")   # a misspelt cap is not silently ignored


def test_the_seeded_policy_page_carries_the_folder_keys_at_their_defaults():
    from pathlib import Path
    seed = Path(__file__).resolve().parents[3] / "behavior" / "global" / "POLICIES.md"
    assert fi.resolve(seed.read_text()) == fi.DEFAULTS
    attrs = fi.front_page.front_matter(seed.read_text())
    for key, value in fi.DEFAULTS.items():
        assert str(attrs.get(key)) == str(value), key


# ── the routes ───────────────────────────────────────────────────────────────────────────────────

def test_a_folder_becomes_a_new_private_workspace_in_one_commit_by_the_person(env):
    c, ws = env
    files = {"README.md": b"# Notes\n", "kg/topic.md": b"words\n", "data/table.csv": b"a,b\n"}
    plan, up, done = _import(c, files)
    assert plan["blocked"] is None and len(plan["accepted"]) == 3
    assert done.status_code == 201, done.text
    out = done.json()
    slot = ws / ".attached" / "u_jane" / out["slug"]
    assert (slot / "kg" / "topic.md").read_bytes() == b"words\n"
    assert (slot / "CLAUDE.md").exists()                      # created by the existing create path
    assert out["name"] == "Research notes" and out["files"] == 3
    log = _git(slot, "log", "-1", "--format=%an|%ae|%s")
    assert log.startswith("jane|jane@example.org|")
    assert sorted(_git(slot, "show", "--name-only", "--format=", "HEAD").split()) == sorted(files)
    active = c.get("/api/workspace/active", headers=H).json()["active"]
    assert out["slug"] in [m["slug"] for m in active]
    view = c.get("/api/workspace/attached", headers=H).json()
    assert view["slots"][out["slug"]]["name"] == "Research notes"
    status = c.get(f"/api/workspace/folder/{plan['plan_id']}/status", headers=H).json()
    assert status["status"] == "completed" and status["result"]["slug"] == out["slug"]


def test_skip_rules_and_gitignore_are_applied_and_listed(env):
    c, ws = env
    files = {".gitignore": b"*.log\n", "a.md": b"a", "run.log": b"x", ".DS_Store": b"x",
             ".env": b"TOKEN=1", "node_modules/x/i.js": b"x"}
    r = c.post("/api/workspace/folder/plan", headers=H,
               json=_manifest(files, gitignores={".gitignore": "*.log\n"}, pruned=[".git"]))
    body = r.json()
    assert sorted(a["path"] for a in body["accepted"]) == [".gitignore", "a.md"]
    reasons = {s["path"]: s["reason"] for s in body["skipped"]}
    assert reasons["run.log"] == fi.REASON_GITIGNORE
    assert reasons[".env"] == fi.REASON_ENV
    assert reasons["node_modules/x/i.js"] == fi.REASON_DEPS
    assert reasons[".DS_Store"] == fi.REASON_SYSTEM
    assert reasons[".git/"] == fi.REASON_GIT
    assert body["counts"][fi.REASON_ENV] == 1


def test_an_env_file_is_refused_even_when_uploaded_against_the_plan(env):
    c, ws = env
    plan = c.post("/api/workspace/folder/plan", headers=H, json=_manifest({"a.md": b"a"})).json()
    r = _upload(c, plan["plan_id"], {"a.md": b"a", ".env": b"SECRET=1"})
    assert r.status_code == 200
    assert {s["path"]: s["reason"] for s in r.json()["skipped"]}[".env"] == fi.REASON_ENV
    staged = list((ws / ".imports" / "u_jane" / "folders").rglob(".env"))
    assert staged == []


def test_a_private_key_inside_an_innocent_name_is_caught_by_content(env):
    c, ws = env
    key = b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n"
    files = {"notes.md": b"fine", "backup.txt": key}
    plan, up, done = _import(c, files)
    assert {s["path"]: s["reason"] for s in up["skipped"]}["backup.txt"] == fi.REASON_SECRET_CONTENT
    out = done.json()
    slot = ws / ".attached" / "u_jane" / out["slug"]
    assert not (slot / "backup.txt").exists() and (slot / "notes.md").exists()
    assert any(s["path"] == "backup.txt" for s in out["skipped"])


def test_limits_come_from_policy_and_are_enforced_on_the_plan_and_the_bytes(env):
    c, ws = env
    _policy(ws, folder_import_max_files=2, folder_import_file_max_mb=1)
    files = {"a.md": b"a", "b.md": b"b", "c.md": b"c"}
    r = c.post("/api/workspace/folder/plan", headers=H, json=_manifest(files)).json()
    assert r["blocked"] and "limit is 2" in r["blocked"]
    assert r["limits"]["max_files"] == 2
    # a refused plan takes no bytes
    assert _upload(c, r["plan_id"], {"a.md": b"a"}).status_code == 409

    big = {"big.bin": b"x" * (MB + 1), "ok.md": b"ok"}
    r2 = c.post("/api/workspace/folder/plan", headers=H, json=_manifest(big)).json()
    assert [a["path"] for a in r2["accepted"]] == ["ok.md"]
    assert {s["path"]: s["reason"] for s in r2["skipped"]}["big.bin"].startswith("larger than 1 MB")
    # bytes larger than the manifest said are refused at the upload, whatever the plan claimed
    lie = c.post("/api/workspace/folder/plan", headers=H, json={"name": "x", "files": [{"path": "f.md", "size": 1}]}).json()
    res = _upload(c, lie["plan_id"], {"f.md": b"y" * (MB + 5)})
    assert {s["path"]: s["reason"] for s in res.json()["skipped"]}["f.md"].startswith("larger than")


def test_a_total_over_the_cap_is_blocked(env):
    c, ws = env
    _policy(ws, folder_import_max_mb=1)
    r = c.post("/api/workspace/folder/plan", headers=H,
               json={"name": "x", "files": [{"path": f"f{i}.bin", "size": 600 * KB} for i in range(2)]}).json()
    assert r["blocked"] and "limit is 1 MB" in r["blocked"]


def test_an_unreadable_policy_refuses_the_import_naming_the_key(env):
    c, ws = env
    _policy(ws, folder_import_max_mb="many")
    r = c.post("/api/workspace/folder/plan", headers=H, json=_manifest({"a.md": b"a"}))
    assert r.status_code == 503 and "folder_import_max_mb" in r.json()["detail"]


def test_a_path_not_in_the_plan_is_not_written(env):
    c, ws = env
    plan = c.post("/api/workspace/folder/plan", headers=H, json=_manifest({"a.md": b"a"})).json()
    r = _upload(c, plan["plan_id"], {"a.md": b"a", "extra.md": b"sneaky"})
    assert {s["path"]: s["reason"] for s in r.json()["skipped"]}["extra.md"] == "not in the plan"
    done = c.post(f"/api/workspace/folder/{plan['plan_id']}/finish", headers=H, json={}).json()
    slot = ws / ".attached" / "u_jane" / done["slug"]
    assert not (slot / "extra.md").exists()


def test_without_an_authenticated_subject_nothing_happens(env):
    c, ws = env
    r = c.post("/api/workspace/folder/plan", json=_manifest({"a.md": b"a"}))
    assert r.status_code == 401


def test_a_plan_belongs_to_the_person_who_made_it(env):
    c, ws = env
    plan = c.post("/api/workspace/folder/plan", headers=H, json=_manifest({"a.md": b"a"})).json()
    bob = {"X-User-Id": "u_bob"}
    assert _upload(c, plan["plan_id"], {"a.md": b"a"}, headers=bob).status_code == 404
    assert c.post(f"/api/workspace/folder/{plan['plan_id']}/finish", headers=bob, json={}).status_code == 404
    assert c.get(f"/api/workspace/folder/{plan['plan_id']}/status", headers=bob).status_code == 404


def test_a_folder_dropped_onto_an_existing_workspace_lands_in_a_subfolder(env):
    c, ws = env
    first = c.post("/api/workspace/new", headers=H, json={"name": "Team"}).json()["slug"]
    files = {"a.md": b"A", "sub/b.md": b"B"}
    plan, up, done = _import(c, files, manifest_extra={"name": "drop", "slug": first, "into": "drop"})
    assert done.status_code == 201, done.text
    out = done.json()
    assert out["slug"] == first and out["path"] == "drop"
    slot = ws / ".attached" / "u_jane" / first
    assert (slot / "drop" / "sub" / "b.md").read_bytes() == b"B"
    # a second drop with the same name does not overwrite the first
    plan2, up2, done2 = _import(c, {"a.md": b"A2"}, manifest_extra={"name": "drop", "slug": first, "into": "drop"})
    assert done2.json()["path"] == "drop-2"
    assert (slot / "drop" / "a.md").read_bytes() == b"A"


def test_an_import_into_a_workspace_you_cannot_write_is_refused(env):
    c, ws = env
    r = c.post("/api/workspace/folder/plan", headers=H,
               json=_manifest({"a.md": b"a"}, slug="_global", into="x"))
    assert r.status_code in (400, 403)
    r2 = c.post("/api/workspace/folder/plan", headers={"X-User-Id": "u_bob"},
                json=_manifest({"a.md": b"a"}, slug="someone-elses", into="x"))
    assert r2.status_code in (403, 404)


def test_the_status_tool_is_declared_for_agents_and_the_writes_need_a_person():
    from pathlib import Path
    agent = Path(__file__).resolve().parents[1]
    tools = json.loads((agent / "mcp.tools.v1.json").read_text())
    names = {t["name"]: t for t in tools["tools"]}
    assert names["workspace_folder_import_status"]["route"]["path"] == "/api/workspace/folder/{plan_id}/status"
    verbs = {(v["method"], v["path"]): v.get("person") for v in json.loads((agent / "routes.v1.json").read_text())["verbs"]}
    for p in ("/agent/workspace/folder/plan", "/agent/workspace/folder/{plan_id}/files",
              "/agent/workspace/folder/{plan_id}/finish"):
        assert verbs.get(("POST", p)) is True, p
