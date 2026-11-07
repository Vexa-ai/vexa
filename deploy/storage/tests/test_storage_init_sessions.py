"""ADR-0048: the session store has its own bucket and its own key, scoped to that bucket alone.
storage-init makes both on the bundled storage and proves the limit: the sessions key reads, writes
and deletes in its bucket and is refused everywhere in the recordings bucket."""
import importlib.util
import io
import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

spec = importlib.util.spec_from_file_location("storage_init_s", Path(__file__).parents[1] / "storage_init.py")
storage_init = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage_init)


class Store:
    """Buckets of objects, the policies on them, and which keys a policy lets in."""

    def __init__(self):
        self.buckets = {"vexa": {"recordings/1.wav": b"rec"}}
        self.policies = {}
        self.accounts = {}

    def allowed(self, ak, bucket, action):
        if ak == "root-key":
            return True
        pol = json.loads(self.policies.get(bucket, '{"Statement": []}'))
        return any(ak in st["Principal"]["AWS"] and action in st["Action"] for st in pol["Statement"])


class Client:
    def __init__(self, store, ak, leaky=False):
        self.s, self.ak, self.leaky = store, ak, leaky

    def _check(self, bucket, action):
        if not (self.leaky or self.s.allowed(self.ak, bucket, action)):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, action)

    def list_buckets(self):
        return {"Buckets": [{"Name": b} for b in self.s.buckets]}

    def head_bucket(self, *, Bucket):
        if Bucket not in self.s.buckets:
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

    def create_bucket(self, *, Bucket):
        self.s.buckets.setdefault(Bucket, {})

    def put_bucket_policy(self, *, Bucket, Policy):
        self.s.policies[Bucket] = Policy

    def put_object(self, *, Bucket, Key, Body):
        self._check(Bucket, "s3:PutObject")
        self.s.buckets[Bucket][Key] = Body

    def get_object(self, *, Bucket, Key):
        self._check(Bucket, "s3:GetObject")
        if Key not in self.s.buckets[Bucket]:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.s.buckets[Bucket][Key])}

    def delete_object(self, *, Bucket, Key):
        self._check(Bucket, "s3:DeleteObject")
        self.s.buckets[Bucket].pop(Key, None)

    def list_objects_v2(self, *, Bucket, Prefix=""):
        self._check(Bucket, "s3:ListBucket")
        return {"Contents": [{"Key": k} for k in self.s.buckets[Bucket] if k.startswith(Prefix)]}


@pytest.fixture
def env(monkeypatch):
    for name in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "MINIO_SECURE", "RECORDING_BUCKET",
                 "BOT_S3_ENDPOINT", "BOT_S3_ACCESS_KEY", "BOT_S3_SECRET_KEY", "BOT_S3_BUCKET",
                 "BOT_USERDATA_S3_PATH", "VEXA_SESSIONS_S3_ENDPOINT", "VEXA_SESSIONS_S3_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STORAGE_ENDPOINT", "http://storage:9000")
    monkeypatch.setenv("MINIO_ENDPOINT", "storage:9000")
    monkeypatch.setenv("MINIO_BUCKET", "vexa")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "root-key")
    monkeypatch.setenv("MINIO_SECRET_KEY", "root-secret")
    monkeypatch.setenv("VEXA_SESSIONS_S3_ACCESS_KEY", "sessions-key")
    monkeypatch.setenv("VEXA_SESSIONS_S3_SECRET_KEY", "sessions-secret")
    monkeypatch.setattr(storage_init, "ensure_scoped_account",
                        lambda ep, rak, rsk, ak, sk: store.accounts.__setitem__(ak, sk) or "created")
    monkeypatch.setattr(storage_init.time, "sleep", lambda s: None)
    store = Store()
    return store


def _wire(monkeypatch, store, leaky=False):
    monkeypatch.setattr(storage_init, "client",
                        lambda ep, ak, sk: Client(store, ak, leaky=leaky and ak == "sessions-key"))


def test_the_sessions_bucket_and_key_are_made_and_scoped(env, monkeypatch, capsys):
    _wire(monkeypatch, env)
    assert storage_init.main() == 0
    assert "vexa-sessions" in env.buckets and env.accounts == {"sessions-key": "sessions-secret"}
    pol = json.loads(env.policies["vexa-sessions"])
    assert {r for st in pol["Statement"] for r in st["Resource"]} <= {
        "arn:aws:s3:::vexa-sessions", "arn:aws:s3:::vexa-sessions/*"}
    assert "vexa" not in env.policies                       # nothing granted on recordings
    assert env.buckets["vexa"] == {"recordings/1.wav": b"rec"}
    assert env.buckets["vexa-sessions"] == {}                # probes cleaned up
    assert "session store" in capsys.readouterr().out


def test_a_sessions_key_that_reaches_recordings_stops_the_start(env, monkeypatch, capsys):
    _wire(monkeypatch, env, leaky=True)
    assert storage_init.main() == 1
    out = capsys.readouterr().out
    assert "STOP" in out and "recordings" in out
    assert env.buckets["vexa"] == {"recordings/1.wav": b"rec"}


def test_the_sessions_bucket_is_never_the_recordings_bucket(env, monkeypatch, capsys):
    _wire(monkeypatch, env)
    monkeypatch.setenv("VEXA_SESSIONS_S3_BUCKET", "vexa")
    assert storage_init.main() == 1
    assert "STOP" in capsys.readouterr().out
    assert env.accounts == {}


@pytest.mark.parametrize("var,root", [("VEXA_SESSIONS_S3_ACCESS_KEY", "MINIO_ACCESS_KEY"),
                                      ("VEXA_SESSIONS_S3_SECRET_KEY", "MINIO_SECRET_KEY")])
def test_a_root_half_is_refused_as_the_sessions_pair(env, monkeypatch, capsys, var, root):
    _wire(monkeypatch, env)
    monkeypatch.setenv(var, storage_init.os.environ[root])
    assert storage_init.main() == 1
    out = capsys.readouterr().out
    assert f"STOP: {var}" in out and storage_init.os.environ[root] not in out
    assert env.accounts == {}


def test_no_sessions_pair_leaves_the_store_to_be_off(env, monkeypatch, capsys):
    _wire(monkeypatch, env)
    monkeypatch.delenv("VEXA_SESSIONS_S3_ACCESS_KEY")
    monkeypatch.delenv("VEXA_SESSIONS_S3_SECRET_KEY")
    assert storage_init.main() == 0
    assert "vexa-sessions" not in env.buckets
