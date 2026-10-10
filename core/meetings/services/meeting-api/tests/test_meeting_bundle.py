"""Meeting export/import between two separate deployments (meeting-bundle.v1), driven through the
SHIPPED unified app (`meeting_api.app.create_app`) twice: two isolated stores, two object stores,
two different deployment secrets. Offline — no docker, no postgres.

Round trip and cross-deployment:
  * export from A, import into B, export again from B: the transcript part and every media part are
    byte-identical (same SHA-256), and the metadata survives;
  * B's meeting has B's fresh id and B's importer as owner; A's ids are provenance only;
  * an exported file carries nothing deployment-bound (no user ids, secrets, share lists, storage
    paths, emails, workspace ids).

Deny:
  * a stranger cannot export (404, like a missing meeting), a share recipient or workspace member
    cannot either (403 — they see a projected meeting, not the whole one), nor can anyone export a
    meeting that is still live (409);
  * an import grants nothing to anyone but the importer;
  * every malicious golden is refused over HTTP with its code and writes nothing;
  * a re-import is refused as a duplicate (409), and a dry run writes nothing.
"""
from __future__ import annotations

import io
import json
import zipfile
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import bundle_goldens as G
from meeting_api.app import create_app
from meeting_api.bundle import deployment_id
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage

OWNER, VIEWER, MEMBER, STRANGER = 11, 12, 13, 14
IMPORTER, OTHER = 77, 78
SECRET_A, SECRET_B = "deployment-a-secret", "deployment-b-secret"
WAV = G.WAV_AUDIO + b"\x01\x02" * 50


def _deployment(secret):
    store, repo, storage = InMemoryTranscriptStore(), InMemoryRecordingRepo(), InMemoryStorage()
    app = create_app(transcript_store=store, recording_repo=repo, storage=storage, token_secret=secret)
    return SimpleNamespace(client=TestClient(app), store=store, repo=repo, storage=storage)


def _h(uid, workspaces=None):
    h = {"x-user-id": str(uid)}
    if workspaces:
        h["x-user-workspaces"] = workspaces
    return h


@pytest.fixture
def a():
    """Deployment A with one finished meeting that carries every kind of thing an export must NOT
    take: a share list, share grants, a webhook secret, a workspace bind, attendee emails, and a
    recording whose object key names the owner."""
    d = _deployment(SECRET_A)
    mid = d.store.seed_meeting(
        user_id=OWNER, platform="google_meet", native_meeting_id="abc-defg-hij", status="completed",
        start_time="2026-10-01T09:00:00Z", end_time="2026-10-01T09:30:00Z",
        data={
            "title": "Quarterly planning",
            "metadata": {"ticket": "OPS-7", "summary": "plan agreed"},
            "transcript_viewers": [VIEWER],
            "workspace_id": "ws-team",
            "share_grants": [{"secret_hash": "deadbeef" * 8, "mode": "open"}],
            "webhook_secret": "whsec_do_not_export",
            "webhook_url": "http://internal-hooks.svc.cluster.local/x",
            "attendees": [{"email": "ada@example.test", "name": "Ada"}, {"email": "bob@example.test"}],
        },
        segments=[
            {"segment_id": "s1", "start": 0.0, "end": 3.0, "speaker": "Ada", "text": "Let us plan.", "language": "en"},
            {"segment_id": "s2", "start": 3.0, "end": 7.5, "speaker": "Grace", "text": "Agreed.\x1b[31m", "language": "en"},
        ],
    )
    key = f"recordings/{OWNER}/555000111222/sess-1/audio/master.wav"
    d.repo.seed(meeting_id=mid, user_id=OWNER, session_uid="sess-1", status="completed")
    d.repo._meetings[mid]["recordings"] = [{
        "id": 555000111222, "meeting_id": mid, "user_id": OWNER, "session_uid": "sess-1", "source": "bot",
        "status": "completed", "created_at": "2026-10-01T09:30:00Z",
        "media_files": [{"id": 1, "type": "audio", "format": "wav", "storage_path": key,
                         "duration_seconds": 450.0, "chunk_count": 0, "is_final": True}],
    }]
    d.storage.blobs[key] = WAV
    d.mid = mid
    return d


@pytest.fixture
def b():
    return _deployment(SECRET_B)


def _export(d, mid, uid=OWNER, **params):
    return d.client.get(f"/meetings/{mid}/export", params=params, headers=_h(uid))


def _import(d, data, uid=IMPORTER, dry_run=False):
    return d.client.post("/meetings/import", params={"dry_run": "true"} if dry_run else None,
                         content=data, headers={**_h(uid), "content-type": "application/zip"})


def _parts(archive: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


# ── round trip across two deployments ───────────────────────────────────────────────────────────
def test_round_trip_into_a_separate_deployment_keeps_transcript_metadata_and_media(a, b):
    exported = _export(a, a.mid)
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/zip"
    assert exported.headers["content-disposition"] == 'attachment; filename="quarterly-planning.meeting-bundle.zip"'
    src = _parts(exported.content)

    imported = _import(b, exported.content)
    assert imported.status_code == 201, imported.text
    new_id = imported.json()["meeting_id"]

    again = _export(b, new_id, uid=IMPORTER)
    assert again.status_code == 200, again.text
    dst = _parts(again.content)
    # The transcript and the media are the SAME bytes after a trip through a second deployment.
    assert sha256(dst["transcript.json"]).hexdigest() == sha256(src["transcript.json"]).hexdigest()
    assert dst["media/recording-1-audio.wav"] == src["media/recording-1-audio.wav"] == WAV
    src_meeting, dst_meeting = json.loads(src["meeting.json"]), json.loads(dst["meeting.json"])
    for k in ("platform", "native_meeting_id", "title", "start_time", "end_time", "participants", "media"):
        assert dst_meeting[k] == src_meeting[k], k
    meta = json.loads(dst["annotations.json"])["metadata"]
    assert meta["ticket"] == "OPS-7" and meta["summary"] == "plan agreed"

    doc = b.client.get(f"/transcripts/by-id/{new_id}", headers=_h(IMPORTER)).json()
    assert doc["status"] == "completed"
    assert [(s["speaker"], s["text"]) for s in doc["segments"]] == [("Ada", "Let us plan."), ("Grace", "Agreed.[31m")]


def test_cross_deployment_ids_are_fresh_and_the_source_is_provenance_only(a, b):
    archive = _export(a, a.mid).content
    manifest = json.loads(_parts(archive)["manifest.json"])
    assert manifest["source"] == {"deployment_id": deployment_id(SECRET_A), "meeting_id": a.mid}
    assert deployment_id(SECRET_A) != deployment_id(SECRET_B)

    body = _import(b, archive).json()
    row = b.client.get(f"/meetings/{body['meeting_id']}", headers=_h(IMPORTER)).json()
    assert row["user_id"] == IMPORTER and row["shared"] is False
    prov = row["data"]["metadata"]["imported_from"]
    assert prov["source_deployment_id"] == deployment_id(SECRET_A)
    assert prov["source_meeting_id"] == a.mid
    assert prov["bundle_id"] == manifest["bundle_id"]
    assert row["data"]["transcript_import"]["source"] == "bundle"
    # A recording landed under the importer's own key space, never the source's path.
    recs = b.repo._meetings[body["meeting_id"]]["recordings"]
    assert recs and all(mf["storage_path"].startswith(f"recordings/{IMPORTER}/")
                        for r in recs for mf in r["media_files"])


def test_an_exported_bundle_carries_nothing_deployment_bound(a):
    archive = _export(a, a.mid).content
    text = b"\n".join(v for k, v in _parts(archive).items() if k.endswith(".json")).decode()
    for leak in ("whsec_do_not_export", "deadbeef", "internal-hooks", "transcript_viewers",
                 "share_grants", "webhook", "ws-team", "workspace_id", "@example.test",
                 "recordings/", "sess-1", "user_id", str(OWNER) + ","):
        assert leak not in text, leak


def test_transcript_only_bundle_imports_without_media(a, b):
    archive = _export(a, a.mid, media="false").content
    assert not any(n.startswith("media/") for n in _parts(archive))
    r = _import(b, archive)
    assert r.status_code == 201, r.text
    assert r.json()["media"] == []
    golden = (G.GOLDEN / "bundles" / "transcript-only.zip").read_bytes()
    assert _import(b, golden).status_code == 201


def test_the_golden_with_audio_bundle_plays_back_byte_identical(b):
    r = _import(b, (G.GOLDEN / "bundles" / "with-audio.zip").read_bytes())
    assert r.status_code == 201, r.text
    mid = r.json()["meeting_id"]
    rec = b.repo._meetings[mid]["recordings"][0]
    mf = rec["media_files"][0]
    b.repo._meetings[mid]["user_id"] = IMPORTER  # the fake repo learns ownership from the row in prod
    raw = b.client.get(f"/recordings/{rec['id']}/media/{mf['id']}/raw", headers=_h(IMPORTER))
    assert raw.status_code == 200, raw.text
    assert raw.content == G.WEBM_AUDIO


# ── export deny ─────────────────────────────────────────────────────────────────────────────────
def test_a_stranger_cannot_export_and_cannot_tell_the_meeting_exists(a):
    r = _export(a, a.mid, uid=STRANGER)
    assert r.status_code == 404
    assert _export(a, 999999, uid=STRANGER).status_code == 404


def test_a_share_recipient_cannot_export(a):
    assert a.client.get(f"/transcripts/by-id/{a.mid}", headers=_h(VIEWER)).status_code == 200
    r = _export(a, a.mid, uid=VIEWER)
    assert r.status_code == 403
    assert "owner" in r.json()["detail"]


def test_a_workspace_member_cannot_export(a):
    r = a.client.get(f"/meetings/{a.mid}/export", headers=_h(MEMBER, "ws-team"))
    assert r.status_code == 403


def test_a_live_meeting_is_not_exported(a):
    a.store._meetings[a.mid]["status"] = "active"
    r = _export(a, a.mid)
    assert r.status_code == 409


# ── import deny ─────────────────────────────────────────────────────────────────────────────────
def test_an_import_grants_access_to_nobody_but_the_importer(a, b):
    new_id = _import(b, _export(a, a.mid).content).json()["meeting_id"]
    row = b.store._meetings[new_id]
    assert row["user_id"] == IMPORTER
    for key in ("transcript_viewers", "share_grants", "workspace_id", "webhook_secret", "webhook_url"):
        assert key not in row["data"], key
    assert b.client.get(f"/meetings/{new_id}", headers=_h(OTHER)).status_code == 404
    assert b.client.get(f"/transcripts/by-id/{new_id}", headers=_h(OTHER)).status_code == 404
    assert _export(b, new_id, uid=OTHER).status_code == 404
    # The source's share recipient and its workspace mean nothing on the importing deployment.
    assert b.client.get(f"/transcripts/by-id/{new_id}", headers=_h(VIEWER, "ws-team")).status_code == 404


def test_a_reimport_is_refused_as_a_duplicate_and_a_dry_run_says_so(a, b):
    archive = _export(a, a.mid).content
    first = _import(b, archive).json()["meeting_id"]
    preview = _import(b, archive, dry_run=True)
    assert preview.status_code == 200 and preview.json()["duplicate_of"] == first
    again = _import(b, archive)
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "duplicate_import"
    assert str(first) in again.json()["detail"]["detail"]
    owned = [m for m in b.store._meetings.values() if m["user_id"] == IMPORTER]
    assert len(owned) == 1
    # A different person importing the same file is a different import.
    assert _import(b, archive, uid=OTHER).status_code == 201


def test_a_dry_run_previews_and_writes_nothing(a, b):
    r = _import(b, _export(a, a.mid).content, dry_run=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True and body["duplicate_of"] is None
    assert body["meeting"]["title"] == "Quarterly planning"
    assert body["segments"] == 2 and body["speakers"] == ["Ada", "Grace"]
    assert body["media"][0]["type"] == "audio" and body["media"][0]["bytes"] == len(WAV)
    assert b.store._meetings == {} and b.storage.blobs == {}


@pytest.mark.parametrize("name", sorted(G.REFUSED))
def test_every_malicious_golden_is_refused_over_http_and_writes_nothing(b, name):
    code = G.REFUSED[name][0]
    r = _import(b, (G.GOLDEN / "refused" / f"{name}.zip").read_bytes())
    assert r.status_code == {"too_large": 413}.get(code, 422), r.text
    assert r.json()["detail"]["code"] == code
    assert b.store._meetings == {} and b.storage.blobs == {}


def test_an_oversize_upload_is_refused_before_it_is_read(b, monkeypatch):
    from meeting_api.bundle import router

    monkeypatch.setattr(router, "MAX_BUNDLE_BYTES", 1024)
    r = _import(b, b"\x00" * 4096)
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "too_large"


def test_a_failed_import_leaves_nothing_behind(a, b, monkeypatch):
    archive = _export(a, a.mid).content

    async def broken_annotate(*args, **kwargs):
        return {"error": "metadata_too_large", "detail": "metadata too large"}

    monkeypatch.setattr(b.store, "annotate_meeting", broken_annotate)
    r = _import(b, archive)
    assert r.status_code == 422
    assert b.store._meetings == {}
    assert b.storage.blobs == {}


def test_imported_text_is_stripped_of_control_characters_and_kept_as_text(b):
    r = _import(b, (G.GOLDEN / "bundles" / "transcript-only.zip").read_bytes())
    doc = b.client.get(f"/transcripts/by-id/{r.json()['meeting_id']}", headers=_h(IMPORTER)).json()
    # Markup in a transcript is words someone said: stored and served as literal text.
    assert doc["segments"][2]["text"] == "Then the migration plan <b>draft</b>."


def test_the_two_routes_are_sealed_in_the_meetings_manifest():
    from meeting_api import route_scopes

    table = route_scopes.ROUTE_SCOPES
    assert table[("GET", "/meetings/{meeting_id}/export")] == frozenset({"tx"})
    assert table[("POST", "/meetings/import")] == frozenset({"tx"})
