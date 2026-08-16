"""The coupling that matters: the link the postman mails is the link the door opens.

Both halves hold the *same* key, which is the only thing that binds them. This test walks the
whole demo in-process — artifact file → MIME through a real SMTP socket → the link extracted
from the delivered message → the door → the personal-instructions write — so a regression in
either half shows up here rather than in Mailpit at 8am.
"""
from __future__ import annotations

import email
import re
from email import policy
from pathlib import Path

from fastapi.testclient import TestClient

from chat_door.app import create_app
from chat_door.artifact import load_artifact
from chat_door.config import DoorConfig, SigningKey
from chat_door.meetings_client import MeetingsClient
from chat_door.postman import build_message, send
from chat_door.store import FileIdentityStore
from chat_door.tokens import TokenSigner, build_magic_link

from conftest import make_meetings_transport
from smtp_stub import SMTPStub

SHARED_KEY = b"one-key-both-halves-hold"
RECIPIENT = "participant@example.test"

ARTIFACT = """**To:** Henry Buisseret
**Meeting:** 2025-08-28 · Google Meet · 59m
**record:** 126 · [open the record](#) *(placeholder link)*

---

**Owed to you**

- The transcript of this meeting.
"""


def test_email_to_door_to_personal_instructions(tmp_path: Path):
    # 1. an artifact on disk, as W2 renders them
    art_dir = tmp_path / "rendered" / "124"
    art_dir.mkdir(parents=True)
    art_path = art_dir / "henry-buisseret.md"
    art_path.write_text(ARTIFACT, "utf-8")

    # 2. the postman mints a link with the shared key and mails the artifact
    signer = TokenSigner(SHARED_KEY)
    artifact = load_artifact(art_path)
    token = signer.issue(kind="link", subject=RECIPIENT, meeting_id=artifact.meeting_id,
                         scope="guest", ttl_seconds=3600)
    built = build_message(artifact, to_email=RECIPIENT,
                          link=build_magic_link("http://door.test", token))
    with SMTPStub() as stub:
        send(built.message, host=stub.host, port=stub.port)

    delivered = email.message_from_string(stub.messages[0].data, policy=policy.default)
    plain = delivered.get_body(preferencelist=("plain",)).get_content()
    link = re.search(r"http://door\.test/door/verify\?t=[\w.\-]+", plain).group(0)

    # 3. the door — a separate object, same key — opens it
    store_dir = tmp_path / "store"
    config = DoorConfig(
        signing_key=SigningKey(SHARED_KEY),
        base_url="http://door.test",
        meetings_url="http://meetings.test",
        store_dir=store_dir,
    )
    store = FileIdentityStore(store_dir)
    app = create_app(
        config,
        signer=TokenSigner(SHARED_KEY),
        store=store,
        meetings=MeetingsClient("http://meetings.test", transport=make_meetings_transport()),
    )
    with TestClient(app) as client:
        assert store.get_user(RECIPIENT) is None  # nothing exists before the click

        page = client.get(link.replace("http://door.test", ""), follow_redirects=True)
        assert page.status_code == 200
        assert "Henry Buisseret" in page.text

        assert store.get_user(RECIPIENT) is not None  # the click made them a user

        client.post("/door/steer",
                    data={"meeting_id": "126", "text": "decisions only, skip the narrative"})

    assert "decisions only, skip the narrative" in store.read_instructions(RECIPIENT)


def test_a_link_from_a_different_key_does_not_open_the_door(tmp_path: Path):
    """The postman and the door must share a key; a mismatch fails closed, loudly."""
    store_dir = tmp_path / "store"
    config = DoorConfig(signing_key=SigningKey(SHARED_KEY), store_dir=store_dir)
    store = FileIdentityStore(store_dir)
    app = create_app(
        config,
        signer=TokenSigner(SHARED_KEY),
        store=store,
        meetings=MeetingsClient("http://meetings.test", transport=make_meetings_transport()),
    )
    foreign = TokenSigner(b"a-different-key").issue(
        kind="link", subject=RECIPIENT, meeting_id="126", scope="guest", ttl_seconds=600
    )
    with TestClient(app) as client:
        resp = client.get("/door/verify", params={"t": foreign})
    assert resp.status_code == 401
    assert "token_signature_invalid" in resp.text
    assert store.get_user(RECIPIENT) is None
