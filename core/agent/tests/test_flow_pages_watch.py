"""THE PAGE OF A FLOW SOMEBODY WROTE, LANDING IN `_global/flows/` (Vexa-ai/vexa#1639).

flows-api renders one page per runtime-authored flow VERSION and has nowhere to put it: compose
gives that service no volumes at all. agent-api holds the writable organisation tier and is already
its writer — `preset_library.top_up` and `global_seed.top_up` run at this same boot. So the page
generator runs on ACTIVATION by being a reconciler here rather than a hook there, and the choice is
load-bearing in three ways this file pins:

  W1  IT WRITES ONE SHAPE AND ONLY ONE. `_global/flows/` holds the seeded `<flow>.md` pages of the
      image's own flows — which an admin may edit and which the seed will never overwrite — beside
      `<flow>@<version>.md`, one per version somebody authored. Two writers, one directory, and the
      whole reason nothing is lost is that neither can name the other's files.
  W2  IT CONVERGES. A hook fires once and, when its write cannot land, leaves a live flow with no
      page and nothing retrying. This runs at boot and every poll: the directory catches up on the
      next pass, which is the rule the two seed top-ups next to it already follow.
  W3  IT IS QUIET WHEN NOTHING CHANGED. `_global` is a git repository; a writer that rewrote
      identical bytes every five seconds would fill its history with commits saying nothing. The
      poll carries an etag per page so the loop does not carry every step's source either.

And one that is not about pages at all: a filename arriving from another service is DATA. `stale`
drops anything that is not the shape this module owns, so "write whatever it names" is never how a
path traversal gets written here.
"""
from __future__ import annotations

import pytest

from control_plane import flow_pages_watch as watch

SEEDED = "post_meeting.md"           # the image's own flow, written by `global_seed.top_up`


def _page(file: str, body: str, **extra) -> dict:
    flow, _, rest = file.partition("@")
    row = {"file": file, "flow": flow, "version": int(rest[:-3] or 0) if rest else 0,
           "status": "active", "etag": watch.etag(body), "body": body}
    row.update(extra)
    return row


def _rig(pages: list):
    """`(index_fn, bodies_fn, fetched)` over a fixed set of pages — the two arguments `reconcile`
    takes so the loop is driven without a socket, and a record of what it actually asked for."""
    fetched: list = []

    def index_fn():
        return [{k: v for k, v in p.items() if k != "body"} for p in pages]

    def bodies_fn(names):
        fetched.append(sorted(names))
        return [p for p in pages if p["file"] in names]

    return index_fn, bodies_fn, fetched


# ── W1 · one shape, and only one ────────────────────────────────────────────────────────────────

def test_a_runtime_page_is_written_where_the_ask_says_it_will_be(tmp_path):
    idx, bod, _ = _rig([_page("mail_the_ops_team@1.md", "---\nkind: flow\n---\n# it\n")])
    assert watch.reconcile(tmp_path, index_fn=idx, bodies_fn=bod) == ["mail_the_ops_team@1.md"]
    written = tmp_path / watch.FLOWS_DIRNAME / "mail_the_ops_team@1.md"
    assert written.read_text(encoding="utf-8").startswith("---\nkind: flow\n")


def test_the_directory_name_is_the_one_flows_api_answers():
    """flows-api returns it on its own route (`{"dir": …}`, from `flows_pages.PAGES_DIR[-1]`), and
    `core/flows/tests/test_flow_author_pages.py` asserts that value is exactly this."""
    assert watch.FLOWS_DIRNAME == "flows"


def test_it_never_touches_a_seeded_image_page(tmp_path):
    """`post_meeting.md` is the seed's and may have been edited by the admin. `post_meeting@5.md` is
    a version somebody authored on top of it. One directory, two writers, no overlap."""
    pages_dir = tmp_path / watch.FLOWS_DIRNAME
    pages_dir.mkdir()
    (pages_dir / SEEDED).write_text("the admin's own edit", encoding="utf-8")
    idx, bod, _ = _rig([_page("post_meeting@5.md", "# five\n")])
    assert watch.reconcile(tmp_path, index_fn=idx, bodies_fn=bod) == ["post_meeting@5.md"]
    assert (pages_dir / SEEDED).read_text(encoding="utf-8") == "the admin's own edit"


def test_a_seeded_page_is_not_even_read(tmp_path):
    pages_dir = tmp_path / watch.FLOWS_DIRNAME
    pages_dir.mkdir()
    (pages_dir / SEEDED).write_text("x", encoding="utf-8")
    (pages_dir / "README.md").write_text("y", encoding="utf-8")
    (pages_dir / "f@1.md").write_text("z", encoding="utf-8")
    assert sorted(watch.on_disk(pages_dir)) == ["f@1.md"]


@pytest.mark.parametrize("name", [
    "../../POLICIES.md", "/etc/passwd", "post_meeting.md", "README.md", "f@1.txt",
    "..@1.md", "f@1.md/../../x", "", "f@x.md",
])
def test_a_filename_that_is_not_this_writers_shape_is_dropped(tmp_path, name):
    """The filename comes from another service. `stale` refuses it rather than fetching it, so the
    write path is never reached with a name this module did not recognise."""
    assert watch.stale([{"file": name, "etag": "deadbeef"}], {}) == []


def test_a_body_arriving_under_a_refused_name_is_still_never_written(tmp_path):
    """Belt and braces, and deliberately: `stale` decides what to ASK for, and the write loop checks
    the name again on what came BACK. A service that answered with a name nobody asked for would
    otherwise be writing into this directory."""
    idx, bod, _ = _rig([_page("ok@1.md", "# ok\n")])

    def hostile(_names):
        return [{"file": "../POLICIES.md", "flow": "x", "version": 1, "body": "gone"}]

    assert watch.reconcile(tmp_path, index_fn=idx, bodies_fn=hostile) == []
    assert not (tmp_path / "POLICIES.md").exists()


# ── W2 · it converges ───────────────────────────────────────────────────────────────────────────

def test_a_page_that_could_not_be_written_is_written_on_the_next_pass(tmp_path):
    """The failure a hook cannot recover from: the directory was not writable at activation. A pass
    later it is, and the page appears — nobody has to notice and re-run anything."""
    blocked = tmp_path / watch.FLOWS_DIRNAME
    blocked.write_text("not a directory", encoding="utf-8")   # mkdir will fail on this
    idx, bod, _ = _rig([_page("f@1.md", "# f\n")])
    assert watch.reconcile(tmp_path, index_fn=idx, bodies_fn=bod) == []
    blocked.unlink()
    assert watch.reconcile(tmp_path, index_fn=idx, bodies_fn=bod) == ["f@1.md"]


def test_a_changed_page_is_rewritten(tmp_path):
    """A retirement changes the page of a version whose row is otherwise untouched: the first line
    becomes *Retired — version 2 is what runs now*. The reconciler is what makes that appear."""
    idx, bod, _ = _rig([_page("f@1.md", "# active\n")])
    watch.reconcile(tmp_path, index_fn=idx, bodies_fn=bod)
    idx2, bod2, _ = _rig([_page("f@1.md", "> **Retired — version 2 is what runs now.**\n")])
    assert watch.reconcile(tmp_path, index_fn=idx2, bodies_fn=bod2) == ["f@1.md"]
    assert "Retired" in (tmp_path / watch.FLOWS_DIRNAME / "f@1.md").read_text(encoding="utf-8")


def test_no_flows_domain_is_silence_and_never_an_exception(tmp_path):
    """A deployment with no flows has no runtime flows and therefore no pages. It is not an error,
    and it must not stop the service that hosts this thread."""
    assert watch.reconcile(tmp_path, index_fn=lambda: [], bodies_fn=lambda n: []) == []
    assert not (tmp_path / watch.FLOWS_DIRNAME).exists()


def test_a_page_with_no_body_is_skipped_rather_than_truncating_the_file(tmp_path):
    idx, _bod, _ = _rig([_page("f@1.md", "# f\n")])
    assert watch.reconcile(tmp_path, index_fn=idx,
                           bodies_fn=lambda n: [{"file": "f@1.md", "body": ""}]) == []


# ── W3 · quiet when nothing changed ─────────────────────────────────────────────────────────────

def test_an_unchanged_page_is_neither_fetched_nor_rewritten(tmp_path):
    pages = [_page("f@1.md", "# f\n")]
    idx, bod, fetched = _rig(pages)
    assert watch.reconcile(tmp_path, index_fn=idx, bodies_fn=bod) == ["f@1.md"]
    before = (tmp_path / watch.FLOWS_DIRNAME / "f@1.md").stat().st_mtime_ns
    assert watch.reconcile(tmp_path, index_fn=idx, bodies_fn=bod) == []
    assert fetched == [["f@1.md"]]              # asked once, on the pass that wrote it
    assert (tmp_path / watch.FLOWS_DIRNAME / "f@1.md").stat().st_mtime_ns == before


def test_only_the_stale_pages_are_asked_for(tmp_path):
    pages = [_page("a@1.md", "# a\n"), _page("b@1.md", "# b\n")]
    idx, bod, fetched = _rig(pages)
    watch.reconcile(tmp_path, index_fn=idx, bodies_fn=bod)
    pages[1] = _page("b@1.md", "# b, differently\n")
    idx2, bod2, fetched2 = _rig(pages)
    assert watch.reconcile(tmp_path, index_fn=idx2, bodies_fn=bod2) == ["b@1.md"]
    assert fetched2 == [["b@1.md"]]


def test_identical_bytes_under_a_disagreeing_etag_are_still_not_rewritten(tmp_path):
    """The etag is one line written twice, in two images that never import each other. If they ever
    computed it differently the cost must be a wasted fetch and never a wasted write."""
    idx, bod, fetched = _rig([_page("f@1.md", "# f\n")])
    watch.reconcile(tmp_path, index_fn=idx, bodies_fn=bod)
    idx2, bod2, fetched2 = _rig([_page("f@1.md", "# f\n", etag="0000000000000000")])
    assert watch.reconcile(tmp_path, index_fn=idx2, bodies_fn=bod2) == []
    assert fetched2 == [["f@1.md"]]             # fetched, compared, not written


def test_the_etag_is_the_content_hash_flows_api_sends():
    import hashlib
    assert watch.etag("hello") == hashlib.sha256(b"hello").hexdigest()[:16]
    assert watch.etag(b"hello") == watch.etag("hello")
    assert len(watch.etag("")) == 16


# ── the thread ──────────────────────────────────────────────────────────────────────────────────

def test_a_non_positive_interval_runs_nothing(tmp_path):
    assert watch.start(tmp_path, interval_sec=0) is None


def test_the_poll_is_inside_the_ten_seconds_flows_submit_promises():
    """`flows_submit` answers `live_within_s: 10`. The page has to be there by the time the flow
    is, or the link the chat gives 404s for the first few seconds of its life."""
    assert 0 < watch.POLL_S <= 10
    assert watch.TIMEOUT_S < watch.POLL_S + 1


def test_it_starts_and_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "index", lambda: [])
    handle = watch.start(tmp_path, interval_sec=30)
    assert handle is not None and handle.thread.is_alive()
    handle.stop()
    handle.thread.join(timeout=5)
    assert not handle.thread.is_alive()


def test_a_failing_pass_does_not_kill_the_loop(tmp_path, monkeypatch):
    """A daemon thread that raises stops reconciling forever, and nothing says so — the page simply
    stops appearing for every flow written from then on."""
    def boom():
        raise RuntimeError("flows said no")

    monkeypatch.setattr(watch, "index", boom)
    handle = watch.start(tmp_path, interval_sec=30)
    assert handle is not None and handle.thread.is_alive()
    handle.stop()


def test_reading_flows_needs_both_the_address_and_the_key(monkeypatch):
    """The operator key, and deliberately not a fallback chain: a read sent with a credential that
    cannot open the route always 401s, which looks exactly like a deployment that runs no flows."""
    from control_plane import publish as publish_mod
    monkeypatch.setattr(publish_mod, "_flows_base", lambda: "")
    monkeypatch.setattr(publish_mod, "_flows_key", lambda: "k")
    assert watch.index() == [] and watch.bodies(["f@1.md"]) == []
    monkeypatch.setattr(publish_mod, "_flows_base", lambda: "http://flows.invalid")
    monkeypatch.setattr(publish_mod, "_flows_key", lambda: "")
    assert watch.index() == []


def test_an_unreachable_flows_api_answers_with_nothing(monkeypatch):
    from control_plane import publish as publish_mod
    monkeypatch.setattr(publish_mod, "_flows_base", lambda: "http://127.0.0.1:1")
    monkeypatch.setattr(publish_mod, "_flows_key", lambda: "k")
    assert watch.index() == []
    assert watch.bodies(["f@1.md"]) == []


def test_asking_for_nothing_makes_no_request(monkeypatch):
    def never(_path):
        raise AssertionError("bodies([]) must not reach the network")

    monkeypatch.setattr(watch, "_get", never)
    assert watch.bodies([]) == []
