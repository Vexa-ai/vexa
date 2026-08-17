"""Gather: the shipped client's routes, its fall-through, and its honesty about absence."""

from __future__ import annotations

import json

import httpx

from conftest import record_payload, transport_for
from vexa_artifact_pipeline import CorpusTransport, HttpMeetingGateway


def test_the_record_keyed_transcript_route_is_preferred():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/meetings/12615":
            return httpx.Response(200, json={"id": 12615, "platform": "teams", "data": {}})
        if request.url.path == "/meetings/12615/transcript":
            return httpx.Response(200, json={"segments": [{"speaker": "a", "text": "hello there"}]})
        return httpx.Response(404, json={})

    record = HttpMeetingGateway("http://m.test", transport=httpx.MockTransport(handler)).fetch("12615")
    assert record.transcript_available
    assert seen == ["/meetings/12615", "/meetings/12615/transcript"]


def test_a_404_on_the_new_route_falls_through_to_the_one_that_ships_today():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meetings/12615":
            return httpx.Response(200, json={"id": 12615, "data": {}})
        if request.url.path == "/meetings/12615/transcript":
            return httpx.Response(405, json={"detail": "method not allowed"})
        if request.url.path == "/transcripts/by-id/12615":
            return httpx.Response(200, json=[{"speaker": "a", "text": "hello there"}])
        return httpx.Response(404, json={})

    record = HttpMeetingGateway("http://m.test", transport=httpx.MockTransport(handler)).fetch("12615")
    assert record.transcript_available and len(record.segments) == 1


def test_no_transcript_route_answering_is_not_the_same_as_an_empty_meeting():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meetings/12615":
            return httpx.Response(200, json={"id": 12615, "data": {}})
        return httpx.Response(404, json={})

    record = HttpMeetingGateway("http://m.test", transport=httpx.MockTransport(handler)).fetch("12615")
    assert record.found and not record.transcript_available
    assert record.note and record.segments == []


def test_an_empty_transcript_says_so_in_its_own_words():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meetings/12615":
            return httpx.Response(200, json={"id": 12615, "data": {}})
        return httpx.Response(200, json={"segments": []})

    record = HttpMeetingGateway("http://m.test", transport=httpx.MockTransport(handler)).fetch("12615")
    assert record.transcript_available and record.segments == []
    assert "empty" in record.note


def test_a_missing_record_is_found_false_with_the_status_in_the_note():
    record = HttpMeetingGateway("http://m.test", transport=transport_for({})).fetch("404")
    assert not record.found and "404" in record.note


def test_the_records_own_id_wins_over_the_one_requested():
    record = HttpMeetingGateway(
        "http://m.test", transport=transport_for({"5174": record_payload(5175)})
    ).fetch("5174")
    assert record.record_id == "5175"
    assert not record.id_matches_request


def test_the_requested_id_is_used_only_when_the_payload_states_none():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meetings/abc":
            return httpx.Response(200, json={"platform": "teams", "data": {}})
        return httpx.Response(200, json={"segments": []})

    record = HttpMeetingGateway("http://m.test", transport=httpx.MockTransport(handler)).fetch("abc")
    assert record.record_id == "abc"


def test_the_corpus_transport_serves_the_same_routes_from_disk(tmp_path):
    (tmp_path / "5174.json").write_text(json.dumps(record_payload(5175)), "utf-8")
    gateway = HttpMeetingGateway("http://m.test", transport=CorpusTransport(tmp_path))

    by_filename = gateway.fetch("5174")
    by_stated_id = gateway.fetch("5175")
    assert by_filename.record_id == by_stated_id.record_id == "5175"
    assert by_filename.transcript_available and by_stated_id.segments
