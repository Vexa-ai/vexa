"""`web_url` on account reads: the broker builds every provider link, from its own metadata.

The agent never builds a Gmail or Calendar address. A Gmail link names the CONNECTED mailbox by
its address (recorded at consent), never an account index such as `/u/0/`, which opens whichever
account the browser lists first. A Calendar link is Google's own `htmlLink`, kept only on a Google
Calendar host."""
import base64
import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import jsonschema
import pytest

from credential_broker import providers
from conftest import OWNER

SCHEMA = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "credential-broker.v1"
                     / "credential-broker.schema.json").read_text())


def conforms(shape, data):
    jsonschema.validate(data, {"$ref": f"#/$defs/{shape}", **{k: v for k, v in SCHEMA.items() if k != "x-routes"}})


def _mail(mid="m1", thread="t1"):
    data = {"id": mid, "payload": {"mimeType": "text/plain", "headers": [{"name": "Subject", "value": "fixture"}],
                                   "body": {"data": base64.urlsafe_b64encode(b"fixture").decode()}}}
    if thread:
        data["threadId"] = thread
    return data


def _read(action, respond, account, **kw):
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        return providers.read_account("google_email", {"access_token": "x"}, action, account=account, http=c, **kw)


def test_gmail_web_url_names_the_connected_account_not_an_index():
    r = _read("gmail.read", lambda req: httpx.Response(200, json=_mail()), "robin.vale@example.test", message_id="m1")
    url = r["message"]["web_url"]
    assert url == "https://mail.google.com/mail/u/robin.vale@example.test/#all/t1"
    assert "/u/0/" not in url and "/u/1/" not in url


@pytest.mark.parametrize("account,segment", [
    ("robin.vale+work@example.test", "robin.vale%2Bwork@example.test"),
    ("o'quill&co=1@example.test", "o%27quill%26co%3D1@example.test"),
    ("a/b#c?d@example.test", "a%2Fb%23c%3Fd@example.test"),
])
def test_gmail_web_url_percent_encodes_the_account_address(account, segment):
    r = _read("gmail.read", lambda req: httpx.Response(200, json=_mail()), account, message_id="m1")
    assert r["message"]["web_url"] == f"https://mail.google.com/mail/u/{segment}/#all/t1"


def test_gmail_web_url_uses_thread_id_and_falls_back_to_message_id():
    assert providers.gmail_web_url("robin@example.test", "t9", "m9").endswith("/#all/t9")
    assert providers.gmail_web_url("robin@example.test", None, "m9").endswith("/#all/m9")
    assert providers.gmail_web_url("robin@example.test", "", "m9").endswith("/#all/m9")


@pytest.mark.parametrize("account", ["", "not-an-address", "a@b@c", "robin@example.test\n", None])
def test_gmail_web_url_is_omitted_without_a_recorded_account(account):
    """No recorded address means no link — never a fallback to an index like /u/0/."""
    r = _read("gmail.read", lambda req: httpx.Response(200, json=_mail()), account, message_id="m1")
    assert "web_url" not in r["message"]


def test_gmail_web_url_refuses_an_id_that_is_not_a_gmail_id():
    assert providers.gmail_web_url("robin@example.test", "../../x", None) == ""
    assert providers.gmail_web_url("robin@example.test", "t1/#evil", None) == ""


def test_search_and_thread_items_each_carry_web_url():
    def respond(req):
        if req.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if "/threads/" in req.url.path:
            return httpx.Response(200, json={"id": "t7", "messages": [_mail("m1", None), _mail("m2", "t7")]})
        return httpx.Response(200, json=_mail("m1", "t1"))
    search = _read("gmail.search", respond, "robin@example.test")
    assert search["messages"][0]["web_url"] == "https://mail.google.com/mail/u/robin@example.test/#all/t1"
    thread = _read("gmail.thread", respond, "robin@example.test", message_id="t7")
    assert {m["web_url"] for m in thread["messages"]} == {"https://mail.google.com/mail/u/robin@example.test/#all/t7"}


def _events(*links):
    items = [{"id": f"e{i}", "summary": "fixture", "htmlLink": link} for i, link in enumerate(links)]
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"items": items}))) as c:
        return providers.read_account("google_calendar", {"access_token": "x"}, "calendar.events",
                                      time_min="2026-10-01T00:00:00Z", time_max="2026-10-02T00:00:00Z", http=c)["events"]


def test_calendar_web_url_passes_google_html_link_through():
    events = _events("https://www.google.com/calendar/event?eid=ZXZ0", "https://calendar.google.com/calendar/event?eid=ZXZ0")
    assert [e["web_url"] for e in events] == [e["htmlLink"] for e in events]


@pytest.mark.parametrize("link", [
    "https://calendar.example.test/event?eid=1",
    "http://www.google.com/calendar/event?eid=1",
    "https://calendar.google.com.example.test/event",
    "https://user@calendar.google.com/event",
    "https://calendar.google.com:8443/event",
    "javascript:alert(1)",
    None,
    42,
])
def test_calendar_html_link_with_a_non_google_host_is_dropped(link):
    assert "web_url" not in _events(link)[0]


def test_route_builds_web_url_from_the_brokers_recorded_account(signed, connection, ready, store, broker):
    cid = connection("google_email")
    store.put(cid, {"owner": OWNER, "value": {"access_token": "x", "expires_at": time.time() + 300}})
    ready(cid)
    broker.sql("UPDATE connections SET account=? WHERE id=?", ("robin.vale+work@example.test", cid))
    real = providers.read_account
    mock = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=_mail())))
    with patch.object(providers, "read_account", lambda *a, **kw: real(*a, **kw, http=mock)):
        r = signed("agent", "POST", f"/api/connections/{cid}/read", {"action": "gmail.read", "message_id": "m1"})
    assert r.status_code == 200, r.text
    assert r.json()["message"]["web_url"] == "https://mail.google.com/mail/u/robin.vale%2Bwork@example.test/#all/t1"
    conforms("AccountReadResponse", r.json())


def test_caller_cannot_name_the_account_for_web_url(signed, connection, ready, store):
    """The address in the link is broker-held metadata; a request that tries to supply it is refused."""
    cid = connection("google_email")
    store.put(cid, {"owner": OWNER, "value": {"access_token": "x", "expires_at": time.time() + 300}})
    ready(cid)
    with patch.object(providers, "read_account", side_effect=AssertionError("must not run")):
        r = signed("agent", "POST", f"/api/connections/{cid}/read",
                   {"action": "gmail.read", "message_id": "m1", "account": "nora@example.test"})
    assert r.status_code == 422
