"""ADR-0048 / ADR-0038: transcript versions go to S3 through boto3 — the client meeting-api uses —
and nowhere else. No directory store, no hand-written request signing."""
from __future__ import annotations

import pytest

from control_plane import session_blobs


class _NoSuchKey(Exception):
    def __init__(self):
        super().__init__("NoSuchKey")
        self.response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class _Body:
    def __init__(self, data):
        self._d = data

    def read(self):
        return self._d

    def close(self):
        pass


class _Boto:
    def __init__(self):
        self.calls, self.objects = [], {}

    def put_object(self, **kw):
        self.calls.append(("put", kw["Bucket"], kw["Key"]))
        self.objects[(kw["Bucket"], kw["Key"])] = kw["Body"]

    def get_object(self, **kw):
        self.calls.append(("get", kw["Bucket"], kw["Key"]))
        if (kw["Bucket"], kw["Key"]) not in self.objects:
            raise _NoSuchKey()
        return {"Body": _Body(self.objects[(kw["Bucket"], kw["Key"])])}

    def delete_object(self, **kw):
        self.calls.append(("delete", kw["Bucket"], kw["Key"]))
        self.objects.pop((kw["Bucket"], kw["Key"]), None)


def test_s3_blobs_round_trip_through_a_boto3_client():
    c = _Boto()
    b = session_blobs.S3Blobs(bucket="vexa-sessions", client=c)
    b.put("sessions/x/v1.json", b"{}")
    assert b.get("sessions/x/v1.json") == b"{}"
    assert b.get("sessions/x/missing.json") is None
    b.delete("sessions/x/v1.json")
    assert c.calls[0] == ("put", "vexa-sessions", "sessions/x/v1.json")
    assert ("delete", "vexa-sessions", "sessions/x/v1.json") in c.calls


def test_a_bad_key_is_refused_before_the_store_is_called():
    c = _Boto()
    b = session_blobs.S3Blobs(bucket="vexa-sessions", client=c)
    with pytest.raises(ValueError):
        b.put("../recordings/x", b"x")
    assert c.calls == []


def test_there_is_no_directory_store_and_no_hand_signed_client():
    assert not hasattr(session_blobs, "DirBlobs")
    assert not hasattr(session_blobs, "_sign")
    src = open(session_blobs.__file__, encoding="utf-8").read()
    assert "hmac" not in src and "AWS4-HMAC-SHA256" not in src


def test_the_client_is_built_path_style_with_boto3(monkeypatch):
    seen = {}

    class _FakeBoto3:
        @staticmethod
        def client(service, **kw):
            seen.update(kw, service=service)
            return _Boto()

    import sys
    monkeypatch.setitem(sys.modules, "boto3", _FakeBoto3)
    b = session_blobs.S3Blobs(bucket="vexa-sessions", endpoint="http://storage:9000",
                              access_key="AK", secret_key="SK", region="us-east-1")
    b.put("sessions/a/v1.json", b"x")
    assert seen["service"] == "s3" and seen["endpoint_url"] == "http://storage:9000"
    assert seen["config"].s3["addressing_style"] == "path"
