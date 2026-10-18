"""The new-chat home (#1783): compositions appended by a background flow, read by the page with no
model call; feedback that re-ranks and never buries a real person; onboarding paced from usage; the
customer profile a person can correct; history the agent can read.

L2: the router over a sqlite session store, with fakes for every source and for the model."""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from control_plane import home, home_media, home_model, home_playbook, home_sources
from control_plane.routers import home as home_router
from control_plane.session_store import SessionStore
from tests._session_fakes import MemBlobs

GOLDEN = Path(__file__).resolve().parents[1] / "contracts" / "home-card.v1" / "golden"
SECRET = "s3cret"
CURRICULUM = (Path(__file__).resolve().parents[3] / "behavior" / "global" / "onboarding.md").read_text()


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_GLOBAL_SEED_DIR", str(Path(__file__).resolve().parents[3] / "behavior" / "global"))
    monkeypatch.setenv("VEXA_HOME_MEDIA_DIR", str(tmp_path / "media-cache"))
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    gdir = tmp_path / "_global"
    gdir.mkdir()
    (gdir / "onboarding.md").write_text(CURRICULUM)
    clock, published, ranks, frames = Clock(), [], [], []
    state = {"meetings": [], "waiting": [], "ranker": None}

    class Notifier:
        def listing(self, *a, **k):
            return {"notifications": []}

        def send_frame(self, who, frame):
            frames.append((who, frame))

    def ranker(cands, stage):
        ranks.append([c["id"] for c in cands])
        return state["ranker"](cands, stage) if state["ranker"] else None

    app = FastAPI()
    app.include_router(home_router.build(
        subject_of=lambda r: r.headers["x-user-id"], session_store=store, notifier=Notifier(),
        _schedule_source=lambda uid: state["meetings"], _global_root=lambda: gdir,
        _internal_caller=lambda r: r.headers.get("x-internal-secret") == SECRET, dispatcher=None,
        home_clock=clock, home_ranker=ranker, home_queue_source=lambda uid: state["waiting"],
        home_publish=lambda *a: published.append(a) or True,
        home_expander=lambda user: state.get("expand"),
        home_colleagues=lambda uid: state.get("colleagues", []),
        home_media_fetch=lambda url: state["fetch"](url)))
    c = TestClient(app)
    yield {"c": c, "store": store, "clock": clock, "published": published, "ranks": ranks, "state": state, "gdir": gdir, "frames": frames}
    store.close()


def _get(env, who="u_ann", **q):
    r = env["c"].get("/api/home", headers={"x-user-id": who}, params=q)
    assert r.status_code == 200, r.text
    return r.json()


def _compose(env, who="u_ann", trigger="home.stale"):
    return env["c"].post("/api/home/compose", headers={"x-user-id": who, "x-internal-secret": SECRET},
                         json={"trigger": trigger})


def _ids(view):
    return [c["id"] for c in view["cards"]]


# ── the spec ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scene", ["busy", "after", "quiet"])
def test_every_mockup_moment_survives_the_server_sanitizer_whole(scene):
    raw = json.loads((GOLDEN / f"HomeDoc.{scene}.json").read_text())
    doc = home.sanitize_view(raw)
    assert doc is not None
    assert _ids(doc) == [c["id"] for c in raw["cards"]]
    assert json.dumps(doc["cards"], sort_keys=True) == json.dumps(home.sanitize_view(doc)["cards"], sort_keys=True)


def test_unknown_blocks_props_and_html_never_reach_the_page():
    doc = home.sanitize_view({"spec": "home-card.v1", "greeting": {"title": "Hi"}, "people": {},
                              "cards": [{"type": "card", "id": "x", "kind": "evil", "onclick": "alert(1)",
                                         "blocks": [{"type": "html", "html": "<script>"},
                                                    {"type": "title", "text": "a" * 900},
                                                    {"type": "action", "label": "Go", "href": "javascript:alert(1)"},
                                                    {"type": "action", "label": "Open", "open": {"page": "../../etc/passwd"}},
                                                    {"type": "action", "label": "Ask", "prompt": "do it"}]}]})
    card = doc["cards"][0]
    assert card["kind"] == "suggestion" and "onclick" not in card
    assert [b["type"] for b in card["blocks"]] == ["title", "action"]
    assert len(card["blocks"][0]["text"]) == home.LIMITS["text"]
    assert card["blocks"][1] == {"type": "action", "label": "Ask", "prompt": "do it"}


def test_fuzzed_compositions_never_raise():
    rnd = random.Random(7)
    base = json.loads((GOLDEN / "HomeDoc.busy.json").read_text())
    junk = [None, 0, -1, 1e309, "", "x" * 5000, [], {}, {"type": "attach"}, True, ["a"], {"type": "card"}]

    def mutate(v, depth=0):
        if depth > 6 or rnd.random() < 0.15:
            return rnd.choice(junk)
        if isinstance(v, dict):
            return {k: mutate(x, depth + 1) for k, x in v.items() if rnd.random() > 0.1}
        if isinstance(v, list):
            return [mutate(x, depth + 1) for x in v] + ([rnd.choice(junk)] if rnd.random() < 0.2 else [])
        return v

    for _ in range(400):
        out = home.sanitize_view(mutate(base))
        assert out is None or isinstance(out["cards"], list)
        if out:
            json.dumps(out)


# ── the page reads, the flow composes ─────────────────────────────────────────────────────

def test_first_visit_composes_once_without_a_model_and_shows_onboarding(env):
    body = _get(env)
    assert env["ranks"] == []                                   # no model on a page view
    assert body["stage"] == "new"
    kinds = [c["kind"] for c in body["view"]["cards"]]
    assert kinds.count("try") == home.ONBOARDING_AT_ONCE        # at most two onboarding cards
    assert _ids(body["view"])[:2] == ["o-connect-calendar", "o-first-meeting"]
    assert env["published"] and env["published"][0][0] == "home.stale"


def test_a_fresh_composition_is_served_from_the_store_and_publishes_nothing(env):
    _get(env)
    env["published"].clear()
    env["clock"].t += 60
    body = _get(env)
    assert body["stale"] is False and env["published"] == [] and env["ranks"] == []


def test_two_hours_on_the_page_asks_the_flow_not_the_model(env):
    _get(env)
    env["published"].clear()
    env["clock"].t += home.EVERY_S + 1
    assert _get(env)["stale"] is True
    assert env["published"][0][0] == "home.stale" and env["ranks"] == []


def test_compose_is_the_flows_door_only(env):
    r = env["c"].post("/api/home/compose", headers={"x-user-id": "u_ann"}, json={})
    assert r.status_code == 403


def test_compose_appends_and_never_overwrites(env):
    _get(env)
    env["clock"].t += home.DEBOUNCE_S + 1
    assert _compose(env).status_code == 200
    hs = home.HomeStore(env["store"], clock=env["clock"])
    assert len(hs.compositions("u_ann")) == 2
    env["clock"].t += 30
    assert _compose(env).json().get("skipped") == "recent"      # debounced
    assert _compose(env, trigger="meeting.completed").json().get("skipped") == "recent"
    env["clock"].t += home.AFTER_MEETING_DEBOUNCE_S
    assert "skipped" not in _compose(env, trigger="meeting.completed").json()


def test_the_model_only_orders_ids_it_was_given(env):
    now = env["clock"].t
    env["state"]["meetings"] = [
        {"id": 1, "title": "Weekly sync", "status": "scheduled", "scheduled_at": now + 1800},
        {"id": 2, "title": "Pilot review", "status": "completed", "end_time": now - 600}]
    env["state"]["ranker"] = lambda cands, stage: {
        "order": ["m-1", "bogus", "m-2"], "wide": ["m-1", "nope"], "attach": [["m-1", "o-connect-calendar"]],
        "greeting": {"title": "Morning", "text": "Two things"}, "signals": {"m-1": "starts soon"},
        "jobs": [{"job": "run the pilot", "evidence": ["m-2"]}], "_model": "small-1"}
    _get(env)
    env["clock"].t += home.DEBOUNCE_S + 1
    out = _compose(env).json()
    assert out["model"] == "small-1"
    view = _get(env)["view"]
    assert _ids(view)[:2] == ["m-1", "m-2"]
    assert view["cards"][0]["wide"] is True and view["cards"][0]["rank"]["signal"] == "starts soon"
    assert any(b["type"] == "attach" and b["card"]["id"] == "o-connect-calendar" for b in view["cards"][0]["blocks"])
    # the model words the day line; the title is always the person's greeting, said at read time
    assert view["greeting"]["text"] == "Two things" and view["greeting"]["title"] != "Morning"
    cust = env["c"].get("/api/home/customer", headers={"x-user-id": "u_ann"}).json()
    assert cust["fields"]["jobs"]["value"][0]["job"] == "run the pilot"


# ── feedback ──────────────────────────────────────────────────────────────────────────────

def _fb(env, **body):
    r = env["c"].post("/api/home/feedback", headers={"x-user-id": "u_ann"}, json=body)
    assert r.status_code == 200, r.text


def test_an_onboarding_step_refused_as_wrong_is_never_offered_again_but_a_snooze_returns(env):
    _get(env)
    _fb(env, card="o-connect-calendar", kind="try", signal="dismiss", reason="wrong")
    _fb(env, card="o-first-meeting", kind="try", signal="snooze")
    env["clock"].t += home.DEBOUNCE_S + 1
    _compose(env)
    ids = _ids(_get(env)["view"])
    assert "o-connect-calendar" not in ids and "o-first-meeting" not in ids
    env["clock"].t += home.SNOOZE_S + 1
    _compose(env)
    ids = _ids(_get(env)["view"])
    assert "o-first-meeting" in ids and "o-connect-calendar" not in ids


def test_a_real_person_waiting_is_demoted_never_hidden_for_good():
    now = 1_000_000.0
    cand = {"card": {"type": "card", "id": "n-1", "kind": "waiting", "person": "p-a",
                     "blocks": [{"type": "row", "text": "Mira mentioned you"}]}, "prior": 0.8, "people": ["p-a"]}
    ev = [{"id": "e", "card": "n-1", "kind": "waiting", "event": "dismissed", "reason": "wrong",
           "people": ["p-a"], "at": now}]
    view, _ = home.arrange([cand], ev, "active", now + 60, people={"p-a": {"name": "Mira", "vexa": True}})
    assert _ids(view) == []
    view, _ = home.arrange([cand], ev, "active", now + home.PERSON_RETURN_S + 1, people={"p-a": {"name": "Mira", "vexa": True}})
    assert _ids(view) == ["n-1"]
    assert view["cards"][0]["rank"]["score"] < 0.8 * home.STAGE_MIX["active"]["waiting"]


def test_feedback_validates_its_signal_and_reason(env):
    r = env["c"].post("/api/home/feedback", headers={"x-user-id": "u_ann"}, json={"card": "x", "signal": "boom"})
    assert r.status_code == 422
    r = env["c"].post("/api/home/feedback", headers={"x-user-id": "u_ann"},
                      json={"card": "x", "signal": "click", "reason": "wrong"})
    assert r.status_code == 422


def test_history_says_what_came_of_each_suggestion_and_is_per_person(env):
    _get(env)
    _fb(env, card="o-first-meeting", kind="try", signal="sent", chat="chat-abc")
    hist = env["c"].get("/api/home/history", headers={"x-user-id": "u_ann"}).json()
    row = next(c for c in hist["cards"] if c["id"] == "o-first-meeting")
    assert row["outcomes"][0]["event"] == "sent" and row["outcomes"][0]["chat"] == "chat-abc"
    other = env["c"].get("/api/home/history", headers={"x-user-id": "u_bob"}).json()
    assert other["cards"] == []


# ── the customer ──────────────────────────────────────────────────────────────────────────

def test_a_correction_wins_over_what_the_next_composition_infers(env):
    _get(env)
    r = env["c"].post("/api/home/customer/correct", headers={"x-user-id": "u_ann"},
                      json={"field": "stage", "value": {"stage": "power", "why": "I run the team"}})
    assert r.status_code == 200
    env["clock"].t += home.DEBOUNCE_S + 1
    _compose(env)
    cust = env["c"].get("/api/home/customer", headers={"x-user-id": "u_ann"}).json()
    assert cust["spec"] == "home-customer.v1"
    assert cust["fields"]["stage"]["value"]["stage"] == "power"
    assert cust["fields"]["stage"]["confidence"] == 1.0
    assert "capabilities" in cust["fields"] and cust["fields"]["capabilities"]["evidence"] is not None


def test_retention_follows_the_company_policy_key(env):
    (env["gdir"] / "POLICIES.md").write_text("---\nhome_history_retention_days: 30\n---\n")
    _get(env)
    env["clock"].t += 31 * 86400
    _compose(env)
    hs = home.HomeStore(env["store"], clock=env["clock"])
    assert len(hs.compositions("u_ann")) == 1
    assert home.retention_days(None) is None


# ── stage, curriculum, model choice ───────────────────────────────────────────────────────

def test_the_stage_is_derived_from_usage():
    now = 10_000_000.0
    assert home.lifecycle({"first_seen": now}, now)[0] == "new"
    assert home.lifecycle({"first_seen": now, "meetings": 1}, now)[0] == "activating"
    assert home.lifecycle({"first_seen": now - 20 * 86400, "meetings": 6}, now)[0] == "active"
    assert home.lifecycle({"first_seen": now, "meetings": 25, "routines": 1}, now)[0] == "power"
    assert home.lifecycle({"first_seen": now - 90 * 86400, "last_visit": now - 30 * 86400}, now)[0] == "dormant"


def test_the_curriculum_is_data_and_done_comes_from_evidence():
    steps = home.parse_curriculum(CURRICULUM)
    assert [s["id"] for s in steps][:3] == ["connect-calendar", "first-meeting", "connect-mail"]
    facts = {"connection:google_calendar": 1, "meetings": 1}
    assert [s["id"] for s in home.next_steps(steps, facts, set(), set())] == ["connect-mail", "think-out-loud"]
    assert home.parse_curriculum("## bad\n- label: x\n- prompt: y\n- done when: clicks>=1\n") == []


class _Cat:
    empty = False

    def __init__(self, ids):
        self.ids = ids
        self._by_id = {i: {"display_name": i} for i in ids}


def test_the_ranking_model_is_the_cheapest_and_never_opus():
    assert home_model.pick(_Cat(["claude-opus-4", "claude-haiku-4"]), env={}) == "claude-haiku-4"
    assert home_model.pick(_Cat(["claude-opus-4"]), env={"VEXA_HOME_MODEL": "claude-opus-4"}) == ""
    assert home_model.pick(_Cat(["big", "gpt-mini"]), env={"VEXA_HOME_MODEL": "big"}) == "big"
    assert home_model.pick(_Cat(["sonnet"]), env={}) == ""
    assert home_model.pick(None) == ""


def test_candidates_cite_ids_not_content():
    now = 5_000_000.0
    cands, _ = home_sources.candidates(now=now, waiting=[{"id": "r1", "say": "Reply to the bank", "since": now - 90000, "flow": "x"}],
                                       notes=[{"id": "n-1", "kind": "chat_mentioned", "actor": "u_mira", "actor_name": "Mira",
                                               "object_kind": "chat", "object_id": "chat-9", "text": "Mira mentioned you in “Plan”",
                                               "created": now - 60}])
    by = {c["card"]["id"]: c for c in cands}
    assert by["n-n-1"]["card"]["blocks"][0]["action"] == {"type": "action", "label": "Open", "open": {"chat": "chat-9"}}
    assert "q-r1" not in by          # a queue item's `say` is an agent instruction, never a card


# ── state from evidence: the dashboard of ongoing work ────────────────────────────────────

def _card(view, cid):
    return next(c for c in view["cards"] if c["id"] == cid)


def _labels(card):
    return [b["label"] for b in card["blocks"] if b["type"] == "action"]


def test_a_meeting_already_prepared_offers_continue_not_prepare(env):
    """The founder's case (2026-10-13): the home offered "Prepare for …" for a meeting whose prep
    already existed. A meeting's own chat (`meet-<row>`) is the evidence; its card continues it."""
    now = env["clock"].t
    env["state"]["meetings"] = [{"id": 41, "title": "Funding model call", "status": "scheduled", "scheduled_at": now + 5400}]
    env["store"].create("u_ann", name="meet-41", title="Prep: Funding model call")
    sid = env["store"].by_name("u_ann", "meet-41")["id"]
    env["store"]._x("UPDATE agent_session SET id = ? WHERE id = ?", ("meet-41", sid))
    env["store"]._x("UPDATE agent_session_participant SET session_id = ? WHERE session_id = ?", ("meet-41", sid))
    card = _card(_get(env)["view"], "m-41")
    assert "Prepare" not in _labels(card)
    go = next(b for b in card["blocks"] if b["type"] == "action" and b["label"] == "Continue")
    assert go["open"] == {"chat": "meet-41"} and go["primary"] is True
    assert card["state"] == "prepared" and card["refs"]["session"] == "meet-41"


def test_an_action_started_from_a_card_continues_its_chat_and_patches_the_open_home(env):
    now = env["clock"].t
    env["state"]["meetings"] = [{"id": 7, "title": "Weekly", "status": "scheduled", "scheduled_at": now + 9000}]
    _get(env)
    _fb(env, card="m-7", kind="meeting", signal="sent", chat="chat-prep7")
    assert env["frames"][-1] == ("u_ann", {"type": "home.item", "item": "m-7", "state": "acted", "chat": "chat-prep7",
                                           "at": env["frames"][-1][1]["at"]})
    env["clock"].t += home.DEBOUNCE_S + 1
    _compose(env)
    card = _card(_get(env)["view"], "m-7")
    assert _labels(card)[0] == "Continue" and "Prepare" not in _labels(card)


def test_the_work_itself_advances_the_item_and_done_takes_it_off(env):
    now = env["clock"].t
    env["state"]["meetings"] = [{"id": 9, "title": "Review", "status": "completed", "end_time": now - 900}]
    _get(env)
    h = {"x-user-id": "u_ann"}
    r = env["c"].post("/api/home/item", headers=h, json={"item": "m-9", "state": "follow_up", "chat": "chat-recap"})
    assert r.status_code == 200
    assert env["frames"][-1][1]["type"] == "home.item" and env["published"][-1][0] == "home.stale"
    env["clock"].t += home.DEBOUNCE_S + 1
    _compose(env)
    card = _card(_get(env)["view"], "m-9")
    assert _labels(card)[0] == "Continue" and card["state"] == "follow_up"
    env["c"].post("/api/home/item", headers=h, json={"item": "m-9", "state": "done"})
    env["clock"].t += home.DEBOUNCE_S + 1
    _compose(env)
    assert "m-9" not in _ids(_get(env)["view"])
    assert env["c"].post("/api/home/item", headers=h, json={"item": "m-9", "state": "exploded"}).status_code == 422
    hist = env["c"].get("/api/home/history", headers=h).json()
    states = [o.get("state") for c in hist["cards"] if c["id"] == "m-9" for o in c["outcomes"]]
    assert states == ["follow_up", "done"]


def test_a_composition_tells_the_open_home(env):
    _get(env)
    env["clock"].t += home.DEBOUNCE_S + 1
    _compose(env)
    assert any(f[1]["type"] == "home.composed" for f in env["frames"])


def test_expand_inserts_blocks_from_the_card_alone_and_drops_anything_else(env):
    h = {"x-user-id": "u_ann"}
    card = {"type": "card", "id": "s-1", "kind": "suggestion", "blocks": [{"type": "title", "text": "Send the model"}]}
    env["state"]["expand"] = {"blocks": [{"type": "text", "text": "Three tiers, drafted six days ago."},
                                         {"type": "html", "html": "<b>x</b>"}, {"type": "action", "label": "Go", "href": "x"}]}
    r = env["c"].post("/api/home/expand", headers=h, json={"item": "s-1", "card": card})
    assert r.status_code == 200 and r.json()["blocks"] == [{"type": "text", "text": "Three tiers, drafted six days ago."}]
    env["state"]["expand"] = None
    assert env["c"].post("/api/home/expand", headers=h, json={"item": "s-1", "card": card}).status_code == 503
    assert any(o["event"] == "expanded" for c in env["c"].get("/api/home/history", headers=h).json()["cards"]
               if c["id"] == "s-1" for o in c["outcomes"])


# ── people you work with, and their pictures ──────────────────────────────────────────────

def test_a_direct_chat_with_a_colleague_is_on_the_home_with_avatar_logo_and_presence(env):
    st = env["state"]
    st["colleagues"] = [{"subject": "u_mira", "name": "Mira Chen", "email": "mira@northwind.example"}]
    sid = "dm-0011223344556677"
    env["store"].create("u_ann", name=sid, title="Mira")
    real = env["store"].by_name("u_ann", sid)["id"]
    env["store"]._x("UPDATE agent_session SET id = ?, last_active = ? WHERE id = ?", (sid, env["clock"].t - 60, real))
    env["store"]._x("UPDATE agent_session_participant SET session_id = ? WHERE session_id = ?", (sid, real))
    env["store"].add_participant(sid, "u_ann", "u_mira", kind="human", role="member")
    view = _get(env)["view"]
    card = _card(view, f"d-{sid}")
    p = view["people"][card["person"]]
    assert p["name"] == "Mira Chen" and p["vexa"] and p["presence"] == "now"
    # no face from the web (UI 15g): no Gravatar key; a numeric account's photo comes from identity
    assert "avatar" not in p and p["logo"] == "d:northwind.example"
    assert card["blocks"][0]["action"] == {"type": "action", "label": "Continue", "primary": True, "open": {"chat": sid}}
    # the colleague turns presence off: the dot goes
    assert env["c"].post("/api/home/presence", headers={"x-user-id": "u_mira"}, json={"visible": False}).status_code == 200
    env["clock"].t += home.DEBOUNCE_S + 1
    _compose(env)
    view = _get(env)["view"]
    assert "presence" not in view["people"][_card(view, f"d-{sid}")["person"]]


def test_the_media_proxy_fetches_only_images_for_its_two_key_shapes_and_caches(env, tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_HOME_MEDIA_DIR", str(tmp_path / "media"))
    calls = []
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64

    def fetch(url):
        calls.append(url)
        if "apple-touch-icon" in url:
            raise RuntimeError("refusing 169.254.169.254: link-local")     # what the guard raises on a bad hop
        if "favicon" in url:
            return png, "image/png", url
        if "gravatar" in url:
            return b"<svg onload=alert(1)>", "image/svg+xml", url             # svg can carry script: refused
        raise AssertionError(url)

    env["state"]["fetch"] = fetch
    c = env["c"]
    r = c.get("/api/home/media/d:northwind.example")
    assert r.status_code == 200 and r.content == png and r.headers["content-type"] == "image/png"
    assert r.headers["content-security-policy"] == "default-src 'none'"
    n = len(calls)
    assert c.get("/api/home/media/d:northwind.example").status_code == 200 and len(calls) == n   # cached
    g = home_media.avatar_key("Mira@Northwind.example")
    assert c.get(f"/api/home/media/{g}").status_code == 404                                         # svg refused
    for bad in ("d:localhost", "d:169.254.169.254", "x:abc", "g:nothex", "d:-bad.example", "d:a..b"):
        assert c.get(f"/api/home/media/{bad}").status_code == 404, bad
    assert home_media.logo_key("someone@gmail.com") is None                                         # no freemail logos
    (env["gdir"] / "POLICIES.md").write_text("---\nexternal_images: off\n---\n")
    assert c.get("/api/home/media/d:northwind.example").status_code == 404


def test_a_picture_too_large_or_not_an_image_is_never_served(tmp_path):
    key = "d:big.example"
    assert home_media.get(key, fetch=lambda u: (b"0" * (home_media.MAX_BYTES + 1), "image/png", u), root=tmp_path) is None
    assert home_media.get("d:html.example", fetch=lambda u: (b"<html>", "text/html", u), root=tmp_path) is None


def test_another_domain_plugs_candidates_in_through_the_source_interface():
    def people_followups(ctx):
        return [{"card": {"type": "card", "id": "pf-anna", "kind": "waiting", "person": "p-anna",
                          "blocks": [{"type": "row", "person": "p-anna", "text": "Owed: re-test steps", "age": "3d",
                                      "action": {"type": "action", "label": "Message", "message": "p-anna"}}]},
                 "prior": 0.8, "title": "Owed: re-test steps", "people": ["p-anna"],
                 "people_map": {"p-anna": {"name": "Anna", "vexa": False, "email": "anna@example.org"}}}]

    def broken(ctx):
        raise RuntimeError("down")

    home_sources.register_source("people_followups", people_followups)
    home_sources.register_source("broken", broken)
    try:
        cands, people = home_sources.candidates(now=1.0, owner="u_ann")
        assert [c["card"]["id"] for c in cands] == ["pf-anna"] and people["p-anna"]["name"] == "Anna"
        view, _ = home.arrange(cands, [], "active", 1.0, people=people)
        assert view["cards"][0]["blocks"][0]["action"] == {"type": "action", "label": "Message", "message": "p-anna"}
    finally:
        home_sources.SOURCES.pop("people_followups", None)
        home_sources.SOURCES.pop("broken", None)


# ── the lifecycle playbook and the purpose mix ───────────────────────────────────────────

PLAYBOOK = (Path(__file__).resolve().parents[3] / "behavior" / "global" / "lifecycle.md").read_text()


def test_the_seeded_playbook_decides_the_stage_top_down_from_evidence():
    stages = home_playbook.parse(PLAYBOOK)
    assert [s["id"] for s in stages] == ["dormant", "drifting", "power", "active", "activating", "new"]
    now = 50_000_000.0
    at = lambda **f: home_playbook.stage_of(stages, {"first_seen": now - 86400 * 30, **f}, now)[0]["id"]  # noqa: E731
    assert at() == "new"
    assert at(meetings=2) == "activating"
    assert at(meetings=6) == "active"
    assert at(meetings=25, routines=1) == "power"
    assert at(meetings=25, routines=1, last_visit=now - 8 * 86400) == "drifting"
    assert at(last_visit=now - 30 * 86400) == "dormant"
    bad = home_playbook.parse("## odd\n- enter when: vibes >= 3\n- weights: work 1\n## rest\n- weights: work 1\n")
    assert home_playbook.stage_of(bad, {}, now)[0]["id"] == "rest"     # a condition it cannot read never matches


def _c(cid, purpose, score):
    return {"card": {"id": cid, "kind": "try"}, "purpose": purpose, "score": score, "source": purpose}


def test_the_mix_caps_teaching_at_two_and_keeps_one_card_that_is_not_work():
    stage = {"id": "new", "weights": {"work": 1, "teaching": 1, "connections": 1, "people": 0.5, "automation": 0},
             "nudges": {"teaching": 5, "connections": 1, "automation": 0}}
    pool = [_c(f"t{i}", "teaching", 0.9) for i in range(4)] + [_c("w1", "work", 0.5), _c("a1", "automation", 0.99)]
    chosen, rec = home_playbook.mix(pool, stage)
    ids = [c["card"]["id"] for c in chosen]
    assert sum(1 for i in ids if i.startswith("t")) == 2 and "a1" not in ids
    assert rec["caps"]["teaching"] == 2 and ("a1", "automation cap 0") in rec["skipped"]
    work_only = [_c(f"w{i}", "work", 0.9) for i in range(6)] + [_c("p1", "people", 0.1)]
    chosen, _ = home_playbook.mix(work_only, {"id": "active", "weights": {"work": 1, "people": 0.5}, "nudges": {}})
    assert any(c["purpose"] == "people" for c in chosen)


def test_discovery_proposes_automations_connections_and_context_teaching():
    now = 10_000_000.0
    sessions = [{"id": f"c{i}", "title": "Check my email", "last_active": now - i * 3600} for i in range(4)]
    sessions.append({"id": "c9", "title": "Notes from the Slack thread", "last_active": now})
    meetings = [{"id": i, "title": "Northwind weekly", "status": "completed"} for i in range(3)]
    out = {c["card"]["id"]: c for c in home_playbook.discover(now=now, sessions=sessions, meetings=meetings, connected=set())}
    auto = out["a-check-my-email"]
    assert auto["purpose"] == "automation" and "flows_submit" in auto["card"]["blocks"][-1]["prompt"]
    assert any(b["type"] == "evidence" for b in auto["card"]["blocks"])
    assert out["k-slack"]["purpose"] == "connections"
    assert out["t-northwind-weekly"]["purpose"] == "teaching"
    assert "k-gmail" not in {c["card"]["id"] for c in home_playbook.discover(
        now=now, sessions=[{"id": "x", "title": "gmail stuff", "last_active": now}], meetings=[], connected={"google_email"})}


def test_an_admin_edit_to_the_playbook_changes_the_next_home_and_the_transition_is_recorded(env):
    _get(env)
    (env["gdir"] / "lifecycle.md").write_text("## everyone\n- goal: x\n- weights: work 1, teaching 1\n- curriculum: none\n")
    env["clock"].t += home.DEBOUNCE_S + 1
    _compose(env)
    body = _get(env)
    assert body["stage"] == "everyone"
    assert not any(c["id"].startswith("o-") for c in body["view"]["cards"])          # no curriculum in scope
    cust = env["c"].get("/api/home/customer", headers={"x-user-id": "u_ann"}).json()
    assert cust["fields"]["transitions"]["value"][-1]["from"] == "new"
    assert cust["fields"]["transitions"]["value"][-1]["to"] == "everyone"


def test_a_meeting_that_ended_since_the_composition_makes_the_home_stale(env):
    _get(env)
    env["published"].clear()
    env["clock"].t += 120
    env["state"]["meetings"] = [{"id": 5, "title": "Sync", "status": "completed", "end_time": env["clock"].t - 30}]
    assert _get(env)["stale"] is True and env["published"][0][0] == "home.stale"


def test_think_out_loud_starts_a_call_not_a_voice_note():
    """Voice notes are retired for a one-speaker call with Vexa (founder, 2026-10-14): the step's
    button starts a call, it is done by a call, and the view keeps the `call` effect."""
    steps = home.parse_curriculum(CURRICULUM)
    think = next(s for s in steps if s["id"] == "think-out-loud")
    assert think["act"] == "call" and think["done_when"] == [("calls", 1)]
    assert not any("voice" in s["label"].lower() for s in steps)
    cands, _ = home_sources.candidates(now=1.0, curriculum=steps, facts={"meetings": 1, "connection:google_calendar": 1, "connection:google_email": 1})
    card = next(c["card"] for c in cands if c["card"]["id"] == "o-think-out-loud")
    act = [b for b in card["blocks"] if b["type"] == "action"][0]
    assert act.get("call") is True and "prompt" not in act
    view = home.sanitize_view({"spec": home.VIEW_SPEC, "greeting": {"title": "Hi"}, "cards": [card]})
    assert [b for b in view["cards"][0]["blocks"] if b["type"] == "action"][0].get("call") is True


def test_the_home_speaks_in_words_a_person_reads():
    """Founder, UI lab 2026-10-14: no "Meet 05:55 PM", no raw slug, no handle, no "Vexa:" preview."""
    now = 1_000_000.0
    meetings = [{"id": 102, "platform": "google_meet", "status": "completed", "end_time": now - 600,
                 "data": {"participants": ["Dmitry Grankin", "Patrick Masson"]}},
                {"id": 103, "platform": "google_meet", "status": "completed", "end_time": now - 300, "title": "jfw-jmbe-pjx",
                 "data": {"title": "Funding-model call"}}]
    sessions = [{"id": "s-r", "title": "unanswered-inbox-sweep", "flow": "user:2/unanswered-inbox-sweep", "last_active": now - 60},
                {"id": "s-c", "title": "Run the Lisbon meetup", "last_active": now - 120},
                {"id": "meet-105", "title": "meet-105", "last_active": now - 30}]
    notes = [{"id": "n1", "kind": "invite_accepted", "actor": "60", "actor_name": "valeriia.bushueva",
              "object_kind": "workspace", "object_id": "ait", "object_name": "AI Tinkerers Lisbon",
              "text": "valeriia.bushueva accepted your invite to “AI Tinkerers Lisbon”", "created": now - 900}]
    lines = {"s-r": "Checked — nothing new.", "s-c": "Vexa: Here is the plan.\n\nCOMMITMENTS IN THIS DRAFT: none.\n\nThe sponsor package goes out on Friday."}
    cands, people = home_sources.candidates(now=now, meetings=meetings, sessions=sessions, notes=notes,
                                            line_of=lambda sid: lines.get(sid, ""),
                                            note_of=lambda m: "---\nmeeting: 103\n---\n# Notes\n\nPrices agreed at three tiers.\n" if m["id"] == 103 else "")
    cards = {c["card"]["id"]: c["card"] for c in cands}
    texts = lambda c: [b.get("text") for b in c["blocks"]]  # noqa: E731
    assert texts(cards["m-102"])[:2] == ["Call with Dmitry Grankin and Patrick Masson", "Writing notes…"]
    assert texts(cards["m-103"])[:2] == ["Funding-model call", "Prices agreed at three tiers."]
    assert cards["rr-s-r"]["blocks"][0]["text"] == "Unanswered inbox sweep · Checked — nothing new."
    assert texts(cards["c-s-c"])[:2] == ["Run the Lisbon meetup", "The sponsor package goes out on Friday."]
    assert "c-meet-105" not in cards                       # a chat nobody named is not a card
    joined = cards["n-n1"]
    assert texts(joined)[1] == "Valeriia Bushueva joined AI Tinkerers Lisbon"
    assert people[joined["person"]]["name"] == "Valeriia Bushueva"
    for c in cards.values():
        assert sum(1 for b in c["blocks"] if b.get("type") == "action" and b.get("primary")) <= 1


def test_previews_read_refs_as_names_routines_once_and_follow_ups_only_when_there_are_some():
    """Founder, live home: raw `[[Dmitry Grankin]]` in a preview; two "Unanswered inbox sweep" rows;
    "Review follow-ups" on a note with none."""
    now = 1_000_000.0
    sessions = [{"id": "s-c", "title": "Lisbon sponsors", "last_active": now - 60},
                {"id": "s-r1", "title": "Unanswered inbox sweep", "flow": "user:2/unanswered-inbox-sweep", "last_active": now - 600},
                {"id": "s-r2", "title": "Unanswered inbox sweep", "flow": "user:2/unanswered-inbox-sweep", "last_active": now - 660}]
    lines = {"s-c": "Spoke with [[Dmitry Grankin]] and [[biz:kg/entities/company/vexum.md]] about the sponsor deck today.",
             "s-r1": "Checked — nothing new."}
    meetings = [{"id": 7, "platform": "google_meet", "status": "completed", "end_time": now - 300, "data": {"title": "Pricing"}},
                {"id": 8, "platform": "google_meet", "status": "completed", "end_time": now - 200, "data": {"title": "Pilot"}}]
    notes = {7: "# Pricing\n\nAgreed three tiers.\n", 8: "# Pilot\n\nScope agreed.\n\n## Follow-ups\n- [ ] Send the deck\n"}
    cands, _ = home_sources.candidates(now=now, sessions=sessions, meetings=meetings, line_of=lambda s: lines.get(s, ""),
                                       note_of=lambda m: notes[m["id"]])
    cards = {c["card"]["id"]: c["card"] for c in cands}
    assert "[[" not in json.dumps(cards) and "Dmitry Grankin" in json.dumps(cards["c-s-c"]) and "vexum" in json.dumps(cards["c-s-c"])
    assert [k for k in cards if k.startswith("rr-")] == ["rr-s-r1"]
    acts = lambda c: [b["label"] for b in c["blocks"] if b["type"] == "action"]  # noqa: E731
    assert acts(cards["m-7"]) == ["Open notes"] and acts(cards["m-8"]) == ["Review follow-ups", "Open notes"]
def test_the_greeting_names_the_person_never_there():
    """Founder, live app.dev: "Hello, there" — "this makes no sense"."""
    g = home.greeting("active", [], "Dmitry Grankin", "Thu 15 Oct", 9)
    assert g["title"] == "Good morning, Dmitry"
    assert home.greeting("active", [], "dmitry.grankin@vexa.ai", "Thu 15 Oct", 20)["title"] == "Good evening, Dmitry"
    assert "there" not in home.greeting("active", [], "", "Thu 15 Oct", 14)["title"]
    view, _ = home.arrange([], [], "active", 1_000_000.0, name="Dmitry", now_local="Thu 15 Oct", hour=15)
    assert view["greeting"]["title"] == "Good afternoon, Dmitry"


def test_home_cards_off_shows_the_greeting_only_and_composes_nothing(env, monkeypatch):
    """Founder 2026-10-15: "for now let's just disable these cards, let's clean up to release." With
    `home_cards` off (the default) the home is the greeting with the person's name; no card, no
    `home.stale`, no composition, no model call — and the compose door refuses to compose."""
    monkeypatch.delenv("VEXA_HOME_CARDS", raising=False)
    now = env["clock"].t
    env["state"]["meetings"] = [{"id": 119, "platform": "google_meet", "status": "active", "start_time": now - 600,
                                 "data": {"title": "Pilot"}}]
    env["state"]["waiting"] = [{"id": "f1", "flow": "x", "status": "failed", "since": now - 60, "reason": {"type": "failed", "detail": "routine_paused"}}]
    before = len(env["published"])
    body = _get(env)
    assert body["view"]["cards"] == [] and body["cards"] is False and body["people"] is True
    assert body["view"]["greeting"]["title"] and "there" not in body["view"]["greeting"]["title"]
    assert len(env["published"]) == before and env["ranks"] == []          # nothing published, no model
    assert _compose(env).json() == {"skipped": "home_cards off"}
    assert env["store"] is not None and home.HomeStore(env["store"], clock=env["clock"]).latest("u_ann") is None
    monkeypatch.setenv("VEXA_HOME_PEOPLE", "off")
    assert _get(env)["people"] is False


def test_the_home_switches_an_explicit_env_wins_over_the_policy_default():
    from control_plane import budgets
    pol = "---\nhome_cards: off\nhome_people: on\n---\n"
    assert budgets.on(budgets.resolve(pol, {}), "home_cards") is False
    assert budgets.on(budgets.resolve(pol, {"VEXA_HOME_CARDS": "on"}), "home_cards") is True
    assert budgets.on(budgets.resolve("---\nhome_cards: on\n---\n", {"VEXA_HOME_CARDS": "off"}), "home_cards") is False
    assert budgets.on(budgets.resolve(pol, {"VEXA_HOME_PEOPLE": "off"}), "home_people") is False


def test_every_greeting_path_names_the_person_at_their_hour(env, monkeypatch):
    """Founder, live app.dev: "Hello, there" under a call in a new chat. Both read paths — cards off
    and a stored composition (whose title the model or a nameless compose worded) — say "Good
    <part of day>, <first name>", from the verified address and the browser's zone on that very read."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = env["clock"].t
    hour = datetime.fromtimestamp(now, ZoneInfo("Europe/Lisbon")).hour
    want = f"Good {home.part_of_day(hour)}, Dmitry"
    hdr = {"x-user-id": "u_ann", "x-user-email": "dmitry.grankin@vexa.ai"}
    # cards off (the release default), first visit: the zone comes with the read
    monkeypatch.delenv("VEXA_HOME_CARDS", raising=False)
    r = env["c"].get("/api/home", headers=hdr, params={"tz": "Europe/Lisbon"}).json()
    assert r["view"]["greeting"]["title"] == want
    # cards on, a stored composition worded "Hello, there": the read re-says the title
    monkeypatch.setenv("VEXA_HOME_CARDS", "on")
    env["c"].get("/api/home", headers=hdr, params={"tz": "Europe/Lisbon"})
    store = home.HomeStore(env["store"], clock=env["clock"])
    latest = store.latest("u_ann")
    assert latest is not None
    view = {**latest["view"], "greeting": {"title": "Hello, there", "text": "Thu 15 Oct · Nothing urgent."}}
    store.append("u_ann", trigger="home.stale", model="m", stage="active", inputs={}, view=view, customer={}, next_due=now + 3600)
    assert store.latest("u_ann")["view"]["greeting"]["title"] == "Hello, there"
    r = env["c"].get("/api/home", headers=hdr, params={"tz": "Europe/Lisbon"}).json()
    assert r["view"]["greeting"]["title"] == want and "there" not in r["view"]["greeting"]["title"]


def test_a_meeting_that_ended_is_never_shown_live(env):
    """Founder, live app.dev: "it's lying, there is no live meeting." A composition made while the
    meeting was live must not keep saying "Live" once meeting-api says it ended — even when the
    background recompose has not run."""
    now = env["clock"].t
    env["state"]["meetings"] = [{"id": 119, "platform": "google_meet", "status": "active",
                                 "start_time": now - 1800, "native_meeting_id": "abc-defg-hij",
                                 "data": {"participants": ["Dmitry", "Valeriia Bushueva"]}}]
    assert _compose(env).status_code == 200
    live = _get(env)["view"]
    assert "Follow live" in json.dumps(live) or "live now" in json.dumps(live)
    env["clock"].t = now + 120
    env["state"]["meetings"] = [{**env["state"]["meetings"][0], "status": "completed", "end_time": now + 60}]
    after = json.dumps(_get(env)["view"])
    assert "Follow live" not in after and "live now" not in after.lower() and "Live now" not in after
    assert "abc-defg-hij" not in after and "google_meet" not in after


def test_the_live_home_founder_saw_is_cleaned_at_the_source(env):
    """Founder, live app.dev home: five "Waiting on you · This one stopped… · Handle" cards, "A meeting
    is happening RIGHT…", a "Colleague · tester-b" card and a "Not shown now" footer — "this just does
    not work". The queue's words are agent instructions; failed and retrying runs stay in their own
    chats; only a paused routine is an ask, in ONE card with a plain row and a real act."""
    now = env["clock"].t
    stopped = "This one stopped, and it will not restart on its own.\n\nIf the reason is about their side…"
    fail = lambda i, flow, detail: {"id": f"f{i}", "flow": flow, "status": "failed", "since": now - 3600 * i,  # noqa: E731
                                    "reason": {"type": "failed", "detail": detail}, "say": stopped}
    env["state"]["waiting"] = [
        fail(1, "user:2/unanswered-inbox-sweep", "no_outcome: the turn did not finish within the hour"),
        fail(2, "post_meeting", "the report is not grounded in the transcript"),
        fail(3, "user:2/unanswered-inbox-sweep", "no_outcome: the turn did not finish within the hour"),
        fail(4, "post_meeting", "the agent turn never finished (no reply after 15min)"),
        fail(5, "user:2/unanswered-inbox-sweep", "unknown flow user:2/unanswered-inbox-sweep@9 — retired by deploy?"),
        {"id": "l1", "flow": "live_meeting", "status": "retrying", "since": now - 60, "reason": {"type": "pending", "detail": ""},
         "say": "A meeting is happening RIGHT now; attend it and …"},
        fail(9, "user:2/inbox-sweep-15m", "agent-api answered 423 to the routine's turn — {'detail': {'refused': 'routine_paused', 'reason': 'human_session_required'}}"),
    ]
    env["state"]["colleagues"] = [{"subject": "54", "name": "tester-b", "email": "tester-b@vexa.test"}]
    assert _compose(env).status_code == 200
    view = _get(env)["view"]
    words = json.dumps(view)
    for gone in ("This one stopped", "Handle", "RIGHT", "tester-b", "not shown now", "Not shown now"):
        assert gone not in words, gone
    paused = [c for c in view["cards"] if c["id"] == "q-paused"]
    assert len(paused) == 1 and paused[0]["label"] == "Needs you"
    [row] = paused[0]["blocks"][0]["rows"]
    assert row["text"].startswith("Inbox sweep 15m paused") and row["action"]["label"] == "Resume"
    assert "left_out" not in view
