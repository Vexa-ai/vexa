"""A CHAT NOBODY NAMED IS NAMED FROM WHAT WAS SAID (founder 2026-10-15: "otherwise most chats will
read New chat"). Six plain words at most, in the chat's language, by the cheap model; never over a
person's own name; never a meeting's or a routine's chat; a few per listing."""
import pytest

from control_plane import chat_autotitle as at

NOW = 1_800_000_000.0
EXCHANGE = [{"role": "user", "text": "can you check what sponsors we still need for the Lisbon meetup"},
            {"role": "agent", "text": "MongoDB is main sponsor; CircleCI is co-sponsor; the third slot is open."}]


@pytest.fixture(autouse=True)
def _fresh():
    at.reset()
    yield
    at.reset()


def _run(rows, turns, complete=None, now=NOW, named=None):
    named = {} if named is None else named
    picked = at.schedule("u1", rows, turns_of=lambda s: turns.get(s, []),
                         name=lambda s, t, n=None, over=False: named.setdefault(s, t) == t, complete=complete, now=now, run=lambda f: f())
    return picked, named


def test_an_untitled_chat_is_named_after_its_first_exchange_in_six_plain_words():
    seen = []
    model = lambda system, user: seen.append(user) or {"title": "“Lisbon meetup sponsors: what is still open?”"}  # noqa: E731
    picked, named = _run([{"session": "pchat-1", "label": ""}], {"pchat-1": EXCHANGE}, model)
    assert picked == ["pchat-1"] and named["pchat-1"] == "Lisbon meetup sponsors what is still"
    assert "sponsors" in seen[0] and "six words" in at.SYSTEM and "language" in at.SYSTEM


def test_not_before_an_answer_never_over_a_person_and_never_a_meeting_or_routine():
    rows = [{"session": "pchat-q", "label": ""},
            {"session": "pchat-h", "label": "", "name_source": "human"},
            {"session": "meet-41", "label": ""},
            {"session": "pchat-m", "label": "", "meeting": "41"},
            {"session": "pchat-r", "label": "", "routine": {"flow": "x"}}]
    turns = {"pchat-q": EXCHANGE[:1], "pchat-h": EXCHANGE, "meet-41": EXCHANGE, "pchat-m": EXCHANGE, "pchat-r": EXCHANGE}
    picked, _ = _run(rows, turns, lambda s, u: {"title": "x"})
    assert picked == []


def test_with_no_model_the_persons_own_first_words_name_it():
    _, named = _run([{"session": "pchat-1", "label": ""}], {"pchat-1": EXCHANGE}, None)
    assert named["pchat-1"] == "Can you check what sponsors we"


def test_a_model_answer_that_is_filler_an_id_or_a_path_is_refused():
    for bad in ("Chat", "pchat-mv8hj24g", "kg/entities/person/x.md", "2026-10-15 notes"):
        assert at.clean(bad) == ""


def test_a_few_per_listing_and_not_every_listing():
    rows = [{"session": f"pchat-{i}", "label": ""} for i in range(5)]
    turns = {f"pchat-{i}": EXCHANGE for i in range(5)}
    first, _ = _run(rows, turns, lambda s, u: {"title": "Sponsors"})
    assert len(first) == at.PER_LISTING
    again, _ = _run(rows, turns, lambda s, u: {"title": "Sponsors"}, now=NOW + 1)
    assert again == []                                   # too soon after the last listing


def test_retitled_once_when_the_chat_has_grown_five_turns():
    named = {}
    _run([{"session": "pchat-1", "label": ""}], {"pchat-1": EXCHANGE}, lambda s, u: {"title": "Sponsors"}, named=named)
    long = EXCHANGE * 3
    picked, _ = _run([{"session": "pchat-1", "label": "Sponsors", "name_source": "agent"}], {"pchat-1": long},
                     lambda s, u: {"title": "Sponsor package pricing"}, now=NOW + 60, named={})
    assert picked == ["pchat-1"]
    picked, _ = _run([{"session": "pchat-1", "label": "Sponsor package pricing", "name_source": "agent"}],
                     {"pchat-1": long * 2}, lambda s, u: {"title": "Other"}, now=NOW + 120, named={})
    assert picked == []                                  # once, not every five turns forever


def test_the_retitle_flag_is_stored_with_the_row_and_survives_a_restart():
    """Coordinator: the "titled at N turns" mark lives with the chat's row, not in process memory."""
    from control_plane.api import _Sessions
    sess = _Sessions()
    sess.upsert("u1", "pchat-1", title="")
    assert sess.name("u1", "pchat-1", "Sponsors", human=False, turns=2)
    row = next(r for r in sess.list("u1") if r["session"] == "pchat-1")
    assert row["auto_titled_turns"] == 2
    at.reset()                                            # a restart: nothing in memory
    long = EXCHANGE * 3
    picked, _ = _run([{**row, "label": "Sponsors"}], {"pchat-1": long}, lambda s, u: {"title": "Sponsor pricing"}, now=NOW + 60)
    assert picked == ["pchat-1"]
    picked, _ = _run([{**row, "label": "Sponsor pricing", "auto_titled_turns": 6}], {"pchat-1": long * 2},
                     lambda s, u: {"title": "x"}, now=NOW + 120)
    assert picked == []


def test_a_local_model_with_no_key_can_title(monkeypatch):
    """app.dev's cheap route is a local Qwen with no key: it must be callable (no Authorization)."""
    from types import SimpleNamespace
    from control_plane import home_model
    monkeypatch.setattr(home_model, "_ctx", lambda: None)
    cat = SimpleNamespace(empty=False, ids=["qwen-local"], _by_id={"qwen-local": {"model": "qwen-local"}},
                          route=lambda mid, ctx, admin=True: SimpleNamespace(credential="", base_url="http://lan:8001/v1",
                                                                              auth_header="bearer", provider_model="qwen"))
    sent = {}

    def post(url, body, headers):
        sent.update(url=url, headers=headers)
        import json
        return 200, json.dumps({"choices": [{"message": {"content": json.dumps({"title": "Lisbon sponsors"})}}], "usage": {}})
    out = home_model.complete_json(cat, at.SYSTEM, "Person: sponsors?", post=post, named="qwen-local")
    assert out and out["title"] == "Lisbon sponsors"
    assert sent["url"].endswith("/v1/chat/completions") and "Authorization" not in sent["headers"]


def test_a_token_stored_as_a_name_is_untitled_and_replaced():
    """Founder, live home: chats named "scaffold-JR3RNMaje4Fglgy6kNoBBeqRlY9OglldE_wHESmKc0g"."""
    over = {}
    picked = at.schedule("u1", [{"session": "pchat-t", "label": "scaffold-JR3RNMaje4Fglgy6kNoBBeqRlY9OglldE_wHESmKc0g", "name_source": "human"},
                                {"session": "pchat-h", "label": "Board prep", "name_source": "human"}],
                         turns_of=lambda s: EXCHANGE, name=lambda s, t, n=None, o=False: over.setdefault(s, (t, o)) is not None,
                         complete=lambda a, b: {"title": "Lisbon sponsors"}, now=NOW, run=lambda f: f())
    assert picked == ["pchat-t"] and over["pchat-t"] == ("Lisbon sponsors", True)
