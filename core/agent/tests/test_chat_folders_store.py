"""Chat folders in the session store — per person, never per chat (ADR-0048, #1783).

`control_plane/chat_folders.py` over a real sqlite session store: the built-in sections (Pinned,
Hidden, Ungrouped), the automatic Routines folder, named folders, filing and moving rows, deleting a
folder (its chats go to Ungrouped, never away), the one-time move of a person's old pin and hide
marks, and that one person's folders are invisible to another on the same shared chat."""
from __future__ import annotations

import pytest

from control_plane import chat_folders as cf
from control_plane import session_blobs
from control_plane.session_store import SessionStore

try:  # the in-memory transcript store the session tests share, where the tree has one
    from tests._session_fakes import MemBlobs as _MemBlobs

    def _blobs(tmp):
        return _MemBlobs()
except ImportError:  # pragma: no cover — an older tree keeps transcripts in a directory
    def _blobs(tmp):
        return session_blobs.DirBlobs(tmp / "b")


@pytest.fixture
def store(tmp_path):
    s = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", _blobs(tmp_path))
    yield s
    s.close()


def _ids(listing):
    return [f["id"] for f in listing["folders"]]


def test_a_new_person_gets_pinned_shared_routines_ungrouped_hidden_in_that_order(store):
    got = cf.listing(store, "a")
    assert _ids(got) == ["pinned", "shared", "routines", "ungrouped", "hidden"]
    by = {f["id"]: f for f in got["folders"]}
    assert by["hidden"]["collapsed"] is True and by["hidden"]["builtin"] is True
    assert by["routines"]["builtin"] is False and by["routines"]["name"] == "Routines"
    assert got["filings"] == {}


def test_create_rename_reorder_collapse_and_delete_a_folder(store):
    a = cf.create(store, "a", "Clients")
    b = cf.create(store, "a", "  Hiring ")
    assert b["name"] == "Hiring"
    # the same name again is the same folder, so an agent asking twice makes one
    assert cf.create(store, "a", "clients")["id"] == a["id"]
    assert _ids(cf.listing(store, "a")) == ["pinned", "shared", "routines", a["id"], b["id"], "ungrouped", "hidden"]
    cf.update(store, "a", b["id"], position=0)
    assert _ids(cf.listing(store, "a")) == ["pinned", "shared", b["id"], "routines", a["id"], "ungrouped", "hidden"]
    cf.update(store, "a", a["id"], name="Customers", collapsed=True)
    f = next(x for x in cf.listing(store, "a")["folders"] if x["id"] == a["id"])
    assert f["name"] == "Customers" and f["collapsed"] is True
    cf.update(store, "a", "hidden", collapsed=False)
    assert next(x for x in cf.listing(store, "a")["folders"] if x["id"] == "hidden")["collapsed"] is False
    with pytest.raises(cf.FolderError) as e:
        cf.update(store, "a", "pinned", name="Top")
    assert e.value.reason == "builtin_folder"
    with pytest.raises(cf.FolderError):
        cf.delete(store, "a", "hidden")


def test_deleting_a_folder_moves_its_chats_to_ungrouped_never_deletes_them(store):
    f = cf.create(store, "a", "Clients")
    cf.file_rows(store, "a", ["c:one", "c:two"], f["id"])
    assert cf.listing(store, "a")["filings"] == {"c:one": f["id"], "c:two": f["id"]}
    moved = cf.delete(store, "a", f["id"])
    assert sorted(moved) == ["c:one", "c:two"]
    got = cf.listing(store, "a")
    assert f["id"] not in _ids(got)
    assert got["filings"] == {"c:one": "ungrouped", "c:two": "ungrouped"}


def test_dissolving_routines_is_remembered(store):
    cf.delete(store, "a", "routines")
    assert "routines" not in _ids(cf.listing(store, "a"))
    assert "routines" not in _ids(cf.listing(store, "a"))


def test_hiding_is_moving_to_hidden_and_unhiding_is_moving_out(store):
    cf.file_rows(store, "a", ["c:one"], "hidden")
    assert cf.marks(store, "a") == {"pinned": [], "hidden": ["c:one"]}
    cf.file_rows(store, "a", ["c:one"], "ungrouped")
    assert cf.marks(store, "a") == {"pinned": [], "hidden": []}


def test_pinned_keeps_a_manual_order(store):
    cf.file_rows(store, "a", ["c:one", "c:two", "c:three"], "pinned")
    assert cf.marks(store, "a")["pinned"] == ["c:one", "c:two", "c:three"]
    cf.file_rows(store, "a", ["c:three"], "pinned", before="c:one")
    assert cf.marks(store, "a")["pinned"] == ["c:three", "c:one", "c:two"]
    assert cf.listing(store, "a")["order"]["pinned"] == ["c:three", "c:one", "c:two"]


def test_a_row_lives_in_one_place(store):
    f = cf.create(store, "a", "Clients")
    cf.file_rows(store, "a", ["c:one"], "pinned")
    cf.file_rows(store, "a", ["c:one"], f["id"])
    assert cf.listing(store, "a")["filings"] == {"c:one": f["id"]}
    assert cf.marks(store, "a")["pinned"] == []


def test_moving_to_a_folder_that_does_not_exist_is_refused(store):
    with pytest.raises(cf.FolderError) as e:
        cf.file_rows(store, "a", ["c:one"], "f-nope")
    assert e.value.reason == "no_such_folder"


def test_old_marks_move_in_once_hidden_wins_and_pins_keep_their_order(store):
    got = cf.listing(store, "a", legacy=lambda: ({"pinned": ["c:p2", "c:p1", "c:both"], "hidden": ["c:h", "c:both"]},
                                                   ["c:p1", "c:p2"]))
    assert got["filings"] == {"c:p1": "pinned", "c:p2": "pinned", "c:both": "hidden", "c:h": "hidden"}
    assert got["order"]["pinned"] == ["c:p1", "c:p2"]
    # a second read never re-imports (the marks may have moved on since)
    cf.file_rows(store, "a", ["c:h"], "ungrouped")
    again = cf.listing(store, "a", legacy=lambda: ({"pinned": [], "hidden": ["c:h"]}, []))
    assert again["filings"]["c:h"] == "ungrouped"


def test_set_mark_is_the_old_pin_and_hide_verb_over_folders(store):
    f = cf.create(store, "a", "Clients")
    cf.file_rows(store, "a", ["c:one"], f["id"])
    cf.set_mark(store, "a", "c:one", pinned=False)          # not pinned: nothing changes
    assert cf.listing(store, "a")["filings"] == {"c:one": f["id"]}
    cf.set_mark(store, "a", "c:one", hidden=True)
    assert cf.marks(store, "a") == {"pinned": [], "hidden": ["c:one"]}
    cf.set_mark(store, "a", "c:one", hidden=False)
    assert cf.listing(store, "a")["filings"] == {}
    cf.set_mark(store, "a", "c:two", pinned=True)
    cf.forget(store, "a", "c:two")
    assert cf.marks(store, "a") == {"pinned": [], "hidden": []}


def test_folders_are_per_person_on_a_shared_chat(store):
    sid = store.create("a", title="Shared")["id"]
    store.add_participant(sid, "a", "b")
    row = f"c:{sid}"
    fa = cf.create(store, "a", "Clients")
    cf.file_rows(store, "a", [row], fa["id"])
    cf.file_rows(store, "b", [row], "hidden")
    assert cf.listing(store, "a")["filings"] == {row: fa["id"]}
    got_b = cf.listing(store, "b")
    assert got_b["filings"] == {row: "hidden"}
    assert fa["id"] not in _ids(got_b)
    # b cannot rename, delete or file into a's folder
    for act in (lambda: cf.update(store, "b", fa["id"], name="Mine"), lambda: cf.delete(store, "b", fa["id"]),
                lambda: cf.file_rows(store, "b", [row], fa["id"])):
        with pytest.raises(cf.FolderError) as e:
            act()
        assert e.value.reason == "no_such_folder"
    assert next(x for x in cf.listing(store, "a")["folders"] if x["id"] == fa["id"])["name"] == "Clients"


def test_names_are_bounded_and_plain(store):
    for bad in ("", "   ", "x" * 61, "a\nb", "pinned", "Hidden", "Ungrouped"):
        with pytest.raises(cf.FolderError):
            cf.create(store, "a", bad)


def test_the_new_tables_are_in_the_schema_of_record():
    from control_plane.session_store import _DDL
    text = " ".join(_DDL)
    assert "agent_session_folder" in text and "agent_session_filing" in text


def test_a_person_made_before_shared_with_me_gets_it_on_the_next_read(store):
    cf.listing(store, "a")
    store._x("DELETE FROM agent_session_folder WHERE owner = ? AND id = ?", ("a", "shared"))
    assert "shared" not in [f["id"] for f in cf._folders(store, "a")]
    assert _ids(cf.listing(store, "a"))[:2] == ["pinned", "shared"]
