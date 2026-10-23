"""Identifiers a workspace page never holds (Vexa-ai/vexa#1783). All values below are published
test or documentation examples, or made up."""
from __future__ import annotations

from shared import sensitive as S

FAKE = {
    "passport": "Power of attorney. Passport no. AB1234567, issued 2020.",
    "passport_ru": "Паспорт 45 06 123456 выдан ОВД",
    "card": "paid with 4111 1111 1111 1111 yesterday",
    "iban": "wire to GB82 WEST 1234 5698 7654 32 please",
    "key": "token sk_live_abcdefghijklmnop1234 here",
    "ssn": "SSN 078-05-1120",
}


def test_each_kind_is_found_and_named_without_its_value():
    for name, text in FAKE.items():
        kinds = S.find(text)
        assert kinds, name
        for kind in kinds:
            assert not any(ch.isdigit() for ch in kind)


def test_ordinary_numbers_are_not_identifiers():
    for text in ("Meeting on 2026-10-13 at 14:00, room 4111.", "Revenue was 12345678 EUR in Q3.",
                 "Call +44 20 7946 0958 tomorrow", "Order 4111111111111112 (not a card)"):
        assert S.find(text) == {}, text


def test_redact_removes_every_match_and_keeps_the_rest():
    text = FAKE["passport"] + "\n" + FAKE["card"]
    out, n = S.redact(text)
    assert n == 2
    assert "AB1234567" not in out and "4111 1111 1111 1111" not in out
    assert "Power of attorney." in out and "paid with" in out


def test_the_refusal_names_kinds_and_counts_only():
    said = S.refusal(S.find(FAKE["passport"] + " " + FAKE["iban"]))
    assert "AB1234567" not in said and "WEST" not in said
    assert "government identifier" in said and "bank account number" in said


# ── the guard on write ───────────────────────────────────────────────────────────────────────────
import pytest
from pathlib import Path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from tests.test_new_person_and_sharing import _client
    seed = tmp_path / "seed"
    (seed / "kg").mkdir(parents=True)
    (seed / "CLAUDE.md").write_text("root\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    c = _client(tmp_path / "ws")
    c.post("/api/workspace/init", headers={"X-User-Id": "47"})
    return c


def test_a_page_with_a_passport_number_is_refused_without_echoing_it(client):
    r = client.put("/api/workspace/file", headers={"X-User-Id": "47"},
                   json={"path": "notes/poa.md", "content": FAKE["passport"]})
    assert r.status_code == 422, r.text
    assert "AB1234567" not in r.text and "government identifier" in r.text


def test_an_entity_fact_with_a_card_number_is_refused(client):
    r = client.post("/api/workspace/entity", headers={"X-User-Id": "47"},
                    json={"kind": "person", "name": "Ada Lovelace", "facts": [FAKE["card"]],
                          "source": "a call"})
    assert r.status_code == 422, r.text
    assert "4111" not in r.text


def test_an_ordinary_page_is_written(client):
    r = client.put("/api/workspace/file", headers={"X-User-Id": "47"},
                   json={"path": "notes/poa.md", "content": "A power of attorney, signed 2026-10-12."})
    assert r.status_code == 200, r.text
