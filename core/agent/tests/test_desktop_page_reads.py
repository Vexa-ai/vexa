"""The workspace page and the attention home read each thing ONCE per request (2026-10-18: the
OeNB workspace page took 15-31 s on app.dev, attention 12-16 s).

py-spy on the live agent-api put the time in reads repeated per name and per tile:
  * every person name on a page (each member of each shared tile, each commit author) walked EVERY
    workspace's roster on the host — `front_page._rosters` per `display_name`;
  * every such name re-read every person page on the named person's desk;
  * the page built the tiles twice (once itself, once through the attention route);
  * every tile re-derived the viewer's mount set.
These tests count the reads, so each fails on the code that repeated them.

L2: the desktop test app (real git workspaces, sqlite session store, fake flows and meetings)."""
from __future__ import annotations

import collections

import pytest

from control_plane import front_page
from control_plane import workspace_membership as m
from control_plane import workspace_names
from control_plane.api_shared import _Sessions
from tests.test_desktop_sections import H, _shared, env  # noqa: F401
from workspaces.shared import workspace_paths as wpaths

PAGE = "/api/desktop/page"
ATTENTION = "/api/desktop/attention"


@pytest.fixture(autouse=True)
def _on(env, monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,home2,readme_page")


def _people_page(env):
    """Two shared workspaces with three people each, and commits by several people."""
    a = _shared(env, "Pilot", "u1", u2="contributor", u3="viewer")
    b = _shared(env, "Partners", "u1", u2="viewer", u3="contributor")
    for who, slug, path in (("u1", a, "README.md"), ("u2", a, "notes.md"), ("u3", b, "plan.md")):
        r = env["c"].put("/api/workspace/file", headers=H(who),
                         json={"path": path, "content": f"# {path}\n\n{{{{people}}}}\n", "slug": slug})
        assert r.status_code == 200, r.text
    return a, b


def _count(monkeypatch, module, name):
    calls = collections.Counter()
    real = getattr(module, name)

    def counted(*args, **kwargs):
        calls[args[1] if len(args) > 1 else None] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, counted)
    return calls


def test_a_page_reads_each_roster_a_bounded_number_of_times(env, monkeypatch):
    a, _b = _people_page(env)
    calls = _count(monkeypatch, m, "read_members")
    r = env["c"].post(PAGE, headers=H("u1"), json={"workspace": a})
    assert r.status_code == 200, r.text
    # one walk of every roster for the names, plus the tiles' and the page's own roster reads —
    # never once per name (the old code read each roster again for every person it named)
    assert max(calls.values()) <= 4, dict(calls)


def test_the_page_builds_the_tiles_once(env, monkeypatch):
    a, _b = _people_page(env)
    listings = []
    real = workspace_names.listing
    monkeypatch.setattr(workspace_names, "listing", lambda *a_, **k: listings.append(1) or real(*a_, **k))
    r = env["c"].post(PAGE, headers=H("u1"), json={"workspace": a})
    assert r.status_code == 200, r.text
    assert len(listings) == 1


def test_a_desk_s_person_pages_are_read_once_per_request(env, monkeypatch):
    a, _b = _people_page(env)
    for who in ("u1", "u2", "u3"):
        d = env["root"] / who / front_page.PERSON_DIR
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{who}.md").write_text(f"---\nself: true\nname: Person {who}\n---\n")
    reads = collections.Counter()
    real = wpaths.list_files_inside

    def counted(base, rel="", *args, **kwargs):
        if rel == front_page.PERSON_DIR:
            reads[str(base)] += 1
        return real(base, rel, *args, **kwargs)

    monkeypatch.setattr(wpaths, "list_files_inside", counted)
    for url, body in ((PAGE, {"workspace": a}), (ATTENTION, {"workspace": a})):
        reads.clear()
        r = env["c"].post(url, headers=H("u1"), json=body)
        assert r.status_code == 200, r.text
        desks = {k: v for k, v in reads.items() if not k.rstrip("/").endswith("_global/" + front_page.PERSON_DIR)
                 and "/_global" not in k}
        assert desks and max(desks.values()) == 1, (url, dict(reads))
        # and the names came out of them
    page = env["c"].post(PAGE, headers=H("u1"), json={"workspace": a}).json()
    people = next(n for n in page["nodes"] if n.get("name") == "people")
    assert {"Person u1 (you)", "Person u2", "Person u3"} <= {row["title"] for row in people["rows"]}


def test_the_same_answer_with_and_without_the_memo(env, monkeypatch):
    """The scope only remembers: with every memo disabled the page and attention say the same."""
    a, _b = _people_page(env)
    first = env["c"].post(PAGE, headers=H("u1"), json={"workspace": a}).json()
    att = env["c"].post(ATTENTION, headers=H("u1"), json={"workspace": a}).json()
    monkeypatch.setattr(m, "scope_memo", lambda key, compute: compute())
    assert env["c"].post(PAGE, headers=H("u1"), json={"workspace": a}).json() == first
    again = env["c"].post(ATTENTION, headers=H("u1"), json={"workspace": a}).json()
    again.pop("generated_at", None)
    att.pop("generated_at", None)
    assert again == att


def test_a_policy_write_inside_a_scope_forgets_what_it_remembered(tmp_path):
    seen = []
    with m.read_scope():
        assert m.scope_memo(("k",), lambda: seen.append(1) or "a") == "a"
        assert m.scope_memo(("k",), lambda: seen.append(1) or "b") == "a"      # remembered
        m.store_put(tmp_path, "ws-a", "members", [])
        assert m.scope_memo(("k",), lambda: seen.append(1) or "c") == "c"      # a write forgets
    assert m.scope_memo(("k",), lambda: "d") == "d"                            # no scope: no memo
    assert len(seen) == 2


def test_chat_targets_are_read_in_one_batch():
    s = _Sessions()
    s.set_target("u1", "chat-a", "ws-a", restart=False)
    got = s.targets([("u1", "chat-a"), ("u1", "chat-b"), ("u1", "chat-a")])
    assert set(got) == {("u1", "chat-a"), ("u1", "chat-b")}
    assert all(got[p] == (s.target(*p) or "") for p in got)
