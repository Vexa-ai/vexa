"""THE HOVER CARD'S SHORT STORY (founder, live: "this should tell the full story, short one"). A
page's frontmatter `summary` — ≤280 chars, ending on a sentence; a page without one is given one
ONCE by the cheap model and it is stored in the page, never regenerated per hover."""
import subprocess

from control_plane import entity_summary as es

PAGE = "---\ntype: person\ntitle: Joe Heitzeberg\n---\n# Joe Heitzeberg\n\nFounder of AI Tinkerers.\n"


def test_read_store_and_clip():
    assert es.read_summary(PAGE) == ""
    stored = es.with_summary(PAGE, "Founder of AI Tinkerers HQ; approves chapter budgets. You run the Lisbon chapter with him.")
    assert es.read_summary(stored).startswith("Founder of AI Tinkerers HQ") and stored.endswith("Founder of AI Tinkerers.\n")
    long = "First sentence here is long enough to count. " * 10
    out = es.clip(long)
    assert len(out) <= es.MAX_CHARS and out.endswith(".")


def test_generated_once_from_the_page_and_never_an_id():
    calls = []
    model = lambda system, user: calls.append(user) or {"summary": "Founder of AI Tinkerers HQ. You run the Lisbon chapter with him."}  # noqa: E731
    assert es.generate(PAGE, model).startswith("Founder of AI Tinkerers HQ") and len(calls) == 1
    assert es.generate(PAGE, lambda s, u: {"summary": "See pchat-mv8hj24g"}) == ""
    assert es.generate(PAGE, None) == ""


def test_the_route_returns_the_stored_story_and_backfills_once(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from control_plane.api import create_app
    from control_plane.dispatch import Dispatcher
    from control_plane.workspace_reader import WorkspaceReader
    from shared.config import load_settings

    class _R:
        def spawn(self, *a): return "x"
        def await_done(self, *a, **k): return "completed"

    class _I:
        def mint(self, *a): return "tok"
    desk = tmp_path / "u_jane"
    (desk / "kg" / "entities" / "person").mkdir(parents=True)
    (desk / "kg" / "entities" / "person" / "joe.md").write_text(PAGE)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=desk, check=True)
    calls = []
    import control_plane.entity_summary as mod
    monkeypatch.setattr(mod, "generate", lambda md, complete: calls.append(1) or "Founder of AI Tinkerers HQ. You run the Lisbon chapter with him.")
    c = TestClient(create_app(Dispatcher(load_settings(workspaces_dir=str(tmp_path)), _R(), _I()), reader=WorkspaceReader(str(tmp_path))))
    h = {"X-User-Id": "u_jane"}
    r = c.post("/api/entities/summary", json={"path": "kg/entities/person/joe.md"}, headers=h)
    assert r.status_code == 200 and r.json()["summary"].startswith("Founder of AI Tinkerers HQ")
    assert es.read_summary((desk / "kg" / "entities" / "person" / "joe.md").read_text()).startswith("Founder")
    again = c.post("/api/entities/summary", json={"path": "kg/entities/person/joe.md"}, headers=h).json()
    assert again["generated"] is False and len(calls) == 1          # stored: never regenerated


def test_a_story_is_never_answered_for_another_desks_page(tmp_path, monkeypatch):
    """The process keeps a generated story only for the same reader and the same page: B's page at
    the same path on B's desk (every desk's slug is "") gets B's own story, never A's."""
    from fastapi.testclient import TestClient
    from control_plane.api import create_app
    from control_plane.dispatch import Dispatcher
    from control_plane.workspace_reader import WorkspaceReader
    from shared.config import load_settings

    class _R:
        def spawn(self, *a): return "x"
        def await_done(self, *a, **k): return "completed"

    class _I:
        def mint(self, *a): return "tok"
    rel = "kg/entities/person/joe.md"
    for who, body in (("u_ann", PAGE), ("u_ben", "---\ntype: person\ntitle: Joe\n---\n# Joe\n")):
        d = tmp_path / who
        (d / "kg" / "entities" / "person").mkdir(parents=True)
        (d / rel).write_text(body)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=d, check=True)
    import control_plane.entity_summary as mod
    monkeypatch.setattr(mod, "generate", lambda md, complete: ("Ann's private story about Joe." if "Tinkerers" in md
                                                                else "Joe, as Ben knows him."))
    c = TestClient(create_app(Dispatcher(load_settings(workspaces_dir=str(tmp_path)), _R(), _I()),
                              reader=WorkspaceReader(str(tmp_path))))
    a = c.post("/api/entities/summary", json={"path": rel}, headers={"X-User-Id": "u_ann"}).json()
    assert a["summary"].startswith("Ann's private story")
    b = c.post("/api/entities/summary", json={"path": rel}, headers={"X-User-Id": "u_ben"}).json()
    assert "Ann" not in b["summary"] and b["summary"] == "Joe, as Ben knows him."
