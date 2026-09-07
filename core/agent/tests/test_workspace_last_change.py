"""`GET /api/workspaces/{slug}/git/last-change` — the front page's second sentence (Vexa-ai/vexa#1634).

Founder, 2026-09-06, on the strip #1628 had just sized down: *"what about this one? never spoke
about how to make it right, helpful and nice."* The line under the title was a git log read out
loud — a commit subject, an author id, a file count — where a person needs *Changed 14 minutes ago
by Jane Smith: the policies wizard ask*.

Neither half of that sentence exists in a git log, and that is the whole reason this route exists.
So the tests below hold exactly those two claims, plus the scope:

  * **THE THING, BY ITS TITLE.** `title:` → the first `# ` heading → the file's own name, and
    several pages become a count. The floor matters as much as the first two: `asks/policies-
    wizard.md` has neither a title nor a heading — it opens with the prompt it is — and *the
    policies wizard ask* is the founder's own words for it, which is its file name read aloud.
  * **THE PERSON, BY THEIR NAME, RESOLVED FROM THEIR ADDRESS** (Vexa-ai/vexa#1642). The address is
    the key and the desk is uid-numbered, so the chain starts by turning one into the other
    (`subject_for_address`) and only then reads: the desk's own person page, the company directory,
    the people record, the identity note, and finally the address's local part read as a name.
    *someone* is not a step — it was what the chain answered when every step above it was reached
    with the wrong key, which is what the founder met on his own instance. An address VERBATIM is
    still never the answer; an address read as a name is the floor under one.
  * **THE SCOPE IS `_read_target`'S**, the same call the file read makes — so this route can
    describe no commit whose page a subject could not open. Held here as a COMPARISON against
    `GET /api/workspace/file`, exactly as `test_workspace_history.py` holds it for the history.

Offline L2 over real git repositories — no docker, no network. The scaffolding is
`test_workspace_history.py`'s, inlined so the module stands alone.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane import front_page
from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _commit(ws: Path, files: dict[str, str], message: str, *, who=("owner1", "owner1@vexa.local")) -> None:
    for path, text in files.items():
        f = ws / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    _git(ws, "add", "-A")
    _git(ws, "-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}", "commit", "-q", "-m", message)


def _init_ws(root: Path, slug: str) -> Path:
    ws = root / slug
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "owner1@vexa.local")
    _git(ws, "config", "user.name", "owner1")
    _commit(ws, {"README.md": "# hello\n"}, "seed")
    return ws


def _client(root: Path, index=None) -> TestClient:
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
        membership_index=index or m.InMemoryMembershipIndex(),
    ))


def _h(subject: str) -> dict:
    return {"X-User-Id": subject}


def _shared(root: Path, slug: str, owner: str = "owner1", subject: str | None = None, role="contributor"):
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(root, slug, owner, index=idx)
    if subject:
        m.grant_membership(root, slug, subject, role, added_by=owner, index=idx)
    return idx


def _desk_person(root: Path, subject: str, page: str) -> None:
    """That subject's own desk, with one person page on it."""
    d = root / subject / front_page.PERSON_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "me.md").write_text(page)


SELF_PAGE = "---\nself: true\nname: Jane Smith\n---\n\n# Jane Smith\n\nShe works here.\n"


def _change(root: Path, slug: str, subject: str, idx=None, **params) -> dict:
    r = _client(root, idx).get(f"/api/workspaces/{slug}/git/last-change",
                               headers=_h(subject), params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ── the thing, by its title ─────────────────────────────────────────────────────────────────────
def test_the_changed_page_comes_back_by_its_frontmatter_title(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _desk_person(tmp_path, "owner1", SELF_PAGE)
    _commit(ws, {"kg/board.md": "---\ntitle: the governing board\n---\n\n# Board\n\nrows\n"},
            "wsA: kg/board.md — added")

    body = _change(tmp_path, "wsA", "owner1", idx)

    change = body["change"]
    assert change["count"] == 1
    assert [p["title"] for p in change["pages"]] == ["the governing board"]
    # …and the sentence's other half, which no git log carries either
    assert change["author"] == "Jane Smith"
    assert change["when"]                       # git's own relative string, read rather than retyped


def test_the_first_heading_when_there_is_no_title(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _commit(ws, {"kg/board.md": "# the governing board\n\nrows\n"}, "wsA: kg/board.md — added")

    assert [p["title"] for p in _change(tmp_path, "wsA", "owner1", idx)["change"]["pages"]] \
        == ["the governing board"]


def test_the_files_own_name_when_it_carries_neither(tmp_path):
    """`asks/policies-wizard.md` has no title and no heading — it opens with the prompt it IS. The
    floor is its own name read aloud, which is what the founder called it in the issue."""
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _commit(ws, {"asks/policies-wizard.md": "---\nlabel: policies\n---\n[policies-wizard] You are…\n"},
            "wsA: asks/policies-wizard.md — added")

    assert [p["title"] for p in _change(tmp_path, "wsA", "owner1", idx)["change"]["pages"]] \
        == ["policies wizard"]


def test_several_pages_are_a_count_and_machinery_is_not_one_of_them(tmp_path):
    """A turn-commit touches several files. The pages are counted; `policy/` and `CLAUDE.md` are not
    pages a person opens, and counting them would say "seven pages" about a change to five."""
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _commit(ws, {**{f"kg/p{i}.md": f"# page {i}\n" for i in range(5)},
                 "policy/notes.md": "# a policy note\n", "CLAUDE.md": "# conventions\n"},
            "wsA: MISSING.md, OBJECTIVES.md +13 — 7 files changed")

    change = _change(tmp_path, "wsA", "owner1", idx)["change"]
    assert change["count"] == 5
    assert sorted(p["path"] for p in change["pages"]) == [f"kg/p{i}.md" for i in range(5)]
    assert all("policy/" not in p["path"] for p in change["pages"])


def test_a_path_narrows_it_to_when_that_page_last_changed(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _commit(ws, {"kg/board.md": "# the governing board\n"}, "wsA: kg/board.md — added")
    _commit(ws, {"kg/other.md": "# something else\n"}, "wsA: kg/other.md — added")

    whole = _change(tmp_path, "wsA", "owner1", idx)["change"]
    scoped = _change(tmp_path, "wsA", "owner1", idx, path="kg/board.md")["change"]

    assert [p["title"] for p in whole["pages"]] == ["something else"]
    assert [p["title"] for p in scoped["pages"]] == ["the governing board"]
    assert scoped["sha"] != whole["sha"]


def test_a_workspace_nothing_has_been_committed_to_answers_with_no_change(tmp_path):
    """An ordinary state of a new workspace, not a failure — a 404 would render as an error."""
    ws = tmp_path / "wsA"
    (ws / "kg").mkdir(parents=True)
    idx = _shared(tmp_path, "wsA")

    body = _change(tmp_path, "wsA", "owner1", idx)
    assert body["change"] is None


# ── the person, by their name ───────────────────────────────────────────────────────────────────
def test_the_author_is_named_from_their_own_self_page(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _desk_person(tmp_path, "owner1", SELF_PAGE)
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added")

    assert _change(tmp_path, "wsA", "owner1", idx)["change"]["author"] == "Jane Smith"


def test_a_person_page_without_self_true_is_not_that_persons_name(tmp_path):
    """A desk holds pages about OTHER people too — `self: true` is what says which one is theirs."""
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _desk_person(tmp_path, "owner1", "---\nname: Somebody Else\n---\n\n# Somebody Else\n")
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added")

    assert _change(tmp_path, "wsA", "owner1", idx)["change"]["author"] is None


def test_the_company_directory_answers_when_the_desk_does_not(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    d = tmp_path / front_page.GLOBAL_SLUG / front_page.PERSON_DIR
    d.mkdir(parents=True)
    (d / "jane-smith.md").write_text("---\nsubject: owner1\nemail: jsmith@example.com\n---\n\n# Jane Smith\n")
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added")

    assert _change(tmp_path, "wsA", "owner1", idx)["change"]["author"] == "Jane Smith"


def test_it_never_answers_with_the_address_itself_but_it_does_read_it(tmp_path):
    """Nothing written down anywhere — the floor (Vexa-ai/vexa#1642).

    Before this issue the answer here was null and the panel printed *someone*, which is the line
    the founder met on the one instance where the person certainly exists. The address is still
    never rendered VERBATIM; it is read as a name, which is a thing the person chose and recognises
    rather than a pronoun that says the product does not know who works here."""
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added",
            who=("jsmith@example.com", "jsmith@example.com"))

    change = _change(tmp_path, "wsA", "owner1", idx)["change"]
    assert change["author"] == "Jsmith"
    assert "@" not in str(change["author"])
    assert str(change["author"]).lower() not in ("someone", "the admin")


def test_a_subject_id_is_never_read_as_a_name(tmp_path):
    """The floor is an ADDRESS read as a name, not any string in the author position.

    `%an` is a subject id on some commits and `%ae` is `<subject>@vexa.local` on every turn commit,
    so a floor that read either aloud would print an internal id dressed as a person — the same
    class of mistake as the address it replaces. Nothing written down, nothing claimed: the sentence
    ends at the time and names nobody."""
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added",
            who=("176", "176@vexa.local"))

    assert _change(tmp_path, "wsA", "owner1", idx)["change"]["author"] is None


# ── the founder's own shape (Vexa-ai/vexa#1642) ─────────────────────────────────────────────────
#
# Seen 2026-09-07 on the dogfood stack: *"Changed 60 minutes ago by **someone**"* on an instance
# whose `_global` log reads `dmitry@vexa.ai|176@vexa.local`, whose desk is `/workspaces/176`, and
# whose person page is `kg/entities/person/dmitry.md` — `type: person / id: dmitry / title: Dmitry`,
# with no `self:` key and no `name:` key. Three separate misses, each of which alone produced
# *someone*:
#
#   1. the route asked for the name of `dmitry@vexa.ai` and that string was joined onto the store
#      root as if it were a subject, so no desk was ever opened;
#   2. the desk's own page carries neither the `self: true` marker nor a `name:` — the two keys the
#      resolver knew — while the schema it was written to uses `title:`;
#   3. with both of those missing there was no floor at all, and `None` printed as a pronoun.
#
# The fixtures below are that shape with this repository's names (`pilot`, `Jane Smith`).

FOUNDER_SHAPE_PAGE = (
    "---\n"
    "type: person\n"
    "id: jsmith\n"
    "title: Jane Smith\n"
    "aliases: []\n"
    "created: 2026-09-06\n"
    "---\n\n"
    "# Jane Smith\n\n"
    "Instance administrator.\n"
)


def _uid_desk(root, subject: str, filename: str, page: str) -> None:
    """A uid-numbered desk with one person page, named the way the KG writes them."""
    d = root / subject / front_page.PERSON_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(page)


def test_the_founders_shape_resolves_to_the_name_on_the_uid_numbered_desk(tmp_path):
    """The whole bug in one test: an address in `%an`, a subject in `%ae`, a uid-numbered desk, and
    a person page that carries `title:` and no `self:`."""
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA", owner="176")
    _uid_desk(tmp_path, "176", "jsmith.md", FOUNDER_SHAPE_PAGE)
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added",
            who=("jsmith@example.com", "176@vexa.local"))

    change = _change(tmp_path, "wsA", "176", idx)["change"]

    assert change["author"] == "Jane Smith"
    # …and not any of the three things it said instead
    assert change["author"] != "Jsmith"          # the floor is under the desk, not over it
    assert change["author"] is not None
    assert "@" not in change["author"]


def test_the_address_alone_opens_no_desk_which_is_why_the_directory_step_exists(tmp_path):
    """The mechanical claim under the test above, stated on its own so a regression names itself."""
    (tmp_path / "176").mkdir()

    assert front_page.subject_for_address(tmp_path, "176@vexa.local") == "176"
    assert not (tmp_path / "jsmith@example.com").exists()


def test_the_people_record_is_the_directory_for_an_ordinary_address(tmp_path):
    """An address that is NOT a mount principal is resolved through the roster — the one file this
    product writes an address and a subject on the same line of."""
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA", owner="176")
    (tmp_path / "wsA" / "policy").mkdir(parents=True, exist_ok=True)
    (tmp_path / "wsA" / "policy" / "members.json").write_text(
        '[{"subject": "176", "role": "owner", "email": "jsmith@example.com"}]')
    _uid_desk(tmp_path, "176", "me.md", SELF_PAGE)
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added",
            who=("jsmith@example.com", "jsmith@example.com"))

    assert front_page.subject_for_address(tmp_path, "jsmith@example.com") == "176"
    assert _change(tmp_path, "wsA", "176", idx)["change"]["author"] == "Jane Smith"


def test_the_roster_name_answers_when_no_page_does(tmp_path):
    (tmp_path / "wsA" / "policy").mkdir(parents=True, exist_ok=True)
    (tmp_path / "wsA" / "policy" / "members.json").write_text(
        '[{"subject": "176", "role": "owner", "email": "jsmith@example.com", "name": "Jane Smith"}]')

    assert front_page.person_name(tmp_path, "176") == "Jane Smith"


def test_the_identity_note_answers_when_no_page_and_no_roster_does(tmp_path):
    """`.system/<subject>/identity.md` is the product's own "who you're helping" note, and the one
    fact `engine.py` tells every agent not to leave blank. One bullet is read out of it."""
    note = tmp_path / front_page.SYSTEM_STORE_DIRNAME / "176"
    note.mkdir(parents=True)
    (note / "identity.md").write_text(
        "# Who you're helping\n\n## User\n\n"
        "- **name:** Jane Smith (jsmith@example.com)\n"
        "- **personal profile:** `kg/entities/person/jsmith.md`\n")

    assert front_page.person_name(tmp_path, "176") == "Jane Smith"


def test_the_identity_stubs_own_placeholder_is_not_a_name(tmp_path):
    """*(unknown — ask the user, then record it here)* is the QUESTION. Printing it on a front page
    would be the product asking a stranger who its own user is."""
    note = tmp_path / front_page.SYSTEM_STORE_DIRNAME / "176"
    note.mkdir(parents=True)
    (note / "identity.md").write_text(
        "## User\n\n- **name:** _(unknown — ask the user, then record it here)_\n")

    assert front_page.identity_name(tmp_path, "176") is None


def test_no_rendered_name_is_ever_someone_or_the_admin(tmp_path):
    """THE ASSERTION THE ISSUE IS NAMED AFTER, over every shape this chain has an answer for."""
    _uid_desk(tmp_path, "176", "jsmith.md", FOUNDER_SHAPE_PAGE)
    answers = [
        front_page.display_name(tmp_path, "176", address="jsmith@example.com"),
        front_page.display_name(tmp_path, address="jsmith@example.com", principal="176@vexa.local"),
        front_page.display_name(tmp_path, address="nobody@example.com"),
        front_page.display_name(tmp_path, address="176", principal="176@vexa.local"),
    ]

    assert answers[:3] == ["Jane Smith", "Jane Smith", "Nobody"]
    assert answers[3] is None                       # nothing to read — the clause is dropped, not filled
    for a in answers:
        assert a is None or "@" not in a
        assert (a or "").lower() not in ("someone", "the admin")


def test_a_page_naming_itself_with_an_address_is_still_not_a_name(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _desk_person(tmp_path, "owner1", "---\nself: true\nname: jsmith@example.com\n---\n")
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added")

    assert _change(tmp_path, "wsA", "owner1", idx)["change"]["author"] is None


def test_the_reader_asks_what_they_are_called(tmp_path):
    _desk_person(tmp_path, "owner1", SELF_PAGE)

    r = _client(tmp_path).get("/api/people/me", headers=_h("owner1"))

    assert r.status_code == 200
    assert r.json() == {"subject": "owner1", "name": "Jane Smith", "first_name": "Jane"}


def test_a_reader_nobody_has_written_down_is_read_off_their_own_address(tmp_path):
    """Vexa-ai/vexa#1642. The company layer's first line reads *<first name> writes it*, and it was
    reading *the admin* on the instance whose administrator is certainly known. `x-user-email` is
    the address the gateway resolved from the session, so it is both the key the chain is written
    against and the floor under it."""
    r = _client(tmp_path).get("/api/people/me",
                              headers={**_h("176"), "X-User-Email": "jsmith@example.com"})

    assert r.status_code == 200
    assert r.json() == {"subject": "176", "name": "Jsmith", "first_name": "Jsmith"}


def test_a_reader_with_neither_a_page_nor_an_address_is_named_null(tmp_path):
    """`null` is still an answer where there is genuinely nothing to read — the panel drops the
    clause rather than inventing a person."""
    r = _client(tmp_path).get("/api/people/me", headers=_h("owner1"))

    assert r.status_code == 200
    assert r.json() == {"subject": "owner1", "name": None, "first_name": None}


def test_the_reader_of_a_uid_numbered_desk_meets_the_name_on_it(tmp_path):
    _uid_desk(tmp_path, "176", "jsmith.md", FOUNDER_SHAPE_PAGE)

    r = _client(tmp_path).get("/api/people/me",
                              headers={**_h("176"), "X-User-Email": "jsmith@example.com"})

    assert r.json() == {"subject": "176", "name": "Jane Smith", "first_name": "Jane"}


# ── who writes the company layer ────────────────────────────────────────────────────────────────
ADMIN_ONLY = "---\nkind: policies\nprofile: default\nglobal_admin_only: on\n---\n\n# Policies\n"


def _company_layer(root, policies: str = ADMIN_ONLY, *, accepted=True):
    """`_global` as the platform seeds it, and (optionally) one acceptance on top of it."""
    g = root / front_page.GLOBAL_SLUG
    g.mkdir(parents=True)
    _git(g, "init", "-q")
    _commit(g, {"POLICIES.md": policies, "README.md": "# Pilot Industries\n"},
            "policy: seed", who=("vexa-platform", "platform@vexa.ai"))
    if accepted:
        _commit(g, {"OBJECTIVES.md": "# Objectives\n"}, "company layer: Pilot Industries",
                who=("jsmith@example.com", "176@vexa.local"))
    return g


def test_the_company_layer_names_its_writer_from_its_own_acceptances(tmp_path):
    """`_global/STRUCTURE.md`: *"every acceptance is a commit authored by the administrator who made
    it"*. So the layer's own history is the record of who writes it — no second store, no new
    credential, no hop to another service."""
    _company_layer(tmp_path)
    _uid_desk(tmp_path, "176", "jsmith.md", FOUNDER_SHAPE_PAGE)

    r = _client(tmp_path).get("/api/people/admin", headers=_h("reader9"))

    assert r.status_code == 200
    assert r.json() == {"name": "Jane Smith", "first_name": "Jane"}


def test_the_administrator_is_not_claimed_when_the_layer_has_more_than_one_writer(tmp_path):
    """With `global_admin_only` off the newest author and *the administrator* are not the same
    person, so the honest answer is nothing rather than the most recent editor promoted."""
    _company_layer(tmp_path, ADMIN_ONLY.replace("global_admin_only: on", "global_admin_only: off"))
    _uid_desk(tmp_path, "176", "jsmith.md", FOUNDER_SHAPE_PAGE)

    assert _client(tmp_path).get("/api/people/admin", headers=_h("reader9")).json() \
        == {"name": None, "first_name": None}


def test_platform_plumbing_is_not_the_administrator(tmp_path):
    """A seed and a policy rewrite are the platform writing, not a person accepting."""
    _company_layer(tmp_path, accepted=False)

    assert _client(tmp_path).get("/api/people/admin", headers=_h("reader9")).json() \
        == {"name": None, "first_name": None}


def test_no_company_layer_is_not_an_error(tmp_path):
    assert _client(tmp_path).get("/api/people/admin", headers=_h("reader9")).status_code == 200


def test_the_roster_carries_names_beside_the_addresses(tmp_path):
    """The front page's FIRST sentence is people ("you, Jane Smith and 2 more"), and the roster is
    where they come from. Additive: a member nobody has written down has no `name` key at all."""
    _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA", subject="member2")
    _desk_person(tmp_path, "member2", SELF_PAGE)

    r = _client(tmp_path, idx).get("/api/workspace/members", params={"workspace_id": "wsA"},
                                   headers=_h("owner1"))

    assert r.status_code == 200
    rows = {row["subject"]: row for row in r.json()["members"]}
    assert rows["member2"]["name"] == "Jane Smith"
    assert "name" not in rows["owner1"]                    # nothing written down → nothing claimed


# ── the scope, held against the file read ───────────────────────────────────────────────────────
def test_a_stranger_is_refused_exactly_as_the_file_read_refuses_them(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    c = _client(tmp_path, idx)

    described = c.get("/api/workspaces/wsA/git/last-change", headers=_h("stranger"))
    read = c.get("/api/workspace/file", params={"path": "README.md", "slug": "wsA"},
                 headers=_h("stranger"))

    assert described.status_code == read.status_code == 403


def test_a_member_is_answered_exactly_as_the_file_read_answers_them(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA", subject="reader1", role="viewer")
    _commit(ws, {"kg/board.md": "# the governing board\n"}, "wsA: kg/board.md — added")
    c = _client(tmp_path, idx)

    described = c.get("/api/workspaces/wsA/git/last-change", headers=_h("reader1"))
    read = c.get("/api/workspace/file", params={"path": "README.md", "slug": "wsA"},
                 headers=_h("reader1"))

    assert described.status_code == read.status_code == 200
    assert [p["title"] for p in described.json()["change"]["pages"]] == ["the governing board"]


def test_system_is_refused_the_way_the_history_refuses_it(tmp_path):
    r = _client(tmp_path).get("/api/workspaces/_system/git/last-change", headers=_h("owner1"))

    assert r.status_code == 403
    assert "sessions and settings" in r.json()["detail"]


def test_personal_is_the_desk(tmp_path):
    """The terminal's desk tab carries no slug, so a path segment needs a word — the same one the
    history route takes."""
    desk = _init_ws(tmp_path, "owner1")
    _commit(desk, {"kg/note.md": "# a note to self\n"}, "personal: kg/note.md — added")

    body = _change(tmp_path, "personal", "owner1")

    assert [p["title"] for p in body["change"]["pages"]] == ["a note to self"]
    assert body["change"]["kind"] == "you"


def test_a_path_that_leaves_the_workspace_is_refused(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")

    r = _client(tmp_path, idx).get("/api/workspaces/wsA/git/last-change",
                                   params={"path": "../../etc/passwd"}, headers=_h("owner1"))

    assert r.status_code == 400
