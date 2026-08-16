"""The dev-only local record source — and the label it is required to carry."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from chat_door.app import create_app
from chat_door.config import DoorConfig, SigningKey
from chat_door.local_records import LocalCorpusRecords
from chat_door.store import FileIdentityStore
from chat_door.tokens import TokenSigner

KEY = b"local-records-key"

CORPUS = {
    "id": 126,
    "platform": "google_meet",
    "data": {"name": "Henry Buisseret"},
    "segments": [{"speaker": "Henry Buisseret", "text": "the bot joined the conversation"}],
}


def write_corpus(root: Path, filename: str = "124.json") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_text(json.dumps(CORPUS), "utf-8")
    return root


def test_resolves_by_the_records_own_id_not_only_the_filename(tmp_path: Path):
    root = write_corpus(tmp_path / "corpus")  # file is 124.json, record id is 126
    record = LocalCorpusRecords(root).fetch("126")
    assert record.found
    assert record.segments[0]["speaker"] == "Henry Buisseret"


def test_missing_record_is_a_stated_miss(tmp_path: Path):
    record = LocalCorpusRecords(write_corpus(tmp_path / "corpus")).fetch("999")
    assert record.found is False
    assert "no corpus file" in record.note


def test_the_page_says_the_record_came_from_a_file(tmp_path: Path):
    root = write_corpus(tmp_path / "corpus")
    config = DoorConfig(
        signing_key=SigningKey(KEY), store_dir=tmp_path / "store", records_dir=root
    )
    signer = TokenSigner(KEY)
    app = create_app(config, signer=signer, store=FileIdentityStore(tmp_path / "store"))
    token = signer.issue(kind="link", subject="x@example.test", meeting_id="126",
                         scope="guest", ttl_seconds=600)
    with TestClient(app) as client:
        page = client.get("/door/verify", params={"t": token}, follow_redirects=True)
    assert page.status_code == 200
    assert "the bot joined the conversation" in page.text
    assert "not from the meeting API" in page.text
