"""Storage readiness uses the application's endpoint and proves actual object I/O offline."""
import importlib.util
import io
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError


spec = importlib.util.spec_from_file_location("storage_init", Path(__file__).parents[1] / "storage_init.py")
storage_init = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage_init)


class FakeS3:
    def __init__(self):
        self.bucket_exists = True
        self.objects = {"recordings/old.wav": b"old recording",
                        "recordings/.vexa-scope-check": b"existing object"}
        self.calls = []
        self.fail = None
        self.unreachable = False
        self.corrupt = False

    def call(self, step, bucket, key=None):
        self.calls.append((step, bucket, key))
        if self.unreachable:
            raise EndpointConnectionError(endpoint_url="http://unreachable.invalid:9000")
        if self.fail == step:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, step)

    def head_bucket(self, *, Bucket):
        self.call("HEAD bucket", Bucket)
        if not self.bucket_exists:
            raise ClientError({"Error": {"Code": "NoSuchBucket"}}, "HeadBucket")

    def create_bucket(self, *, Bucket):
        self.call("CREATE bucket", Bucket)
        self.bucket_exists = True

    def put_object(self, *, Bucket, Key, Body):
        self.call("PUT probe", Bucket, Key)
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        self.call("GET probe", Bucket, Key)
        return {"Body": io.BytesIO(b"wrong bytes" if self.corrupt else self.objects[Key])}

    def delete_object(self, *, Bucket, Key):
        self.call("DELETE probe", Bucket, Key)
        self.objects.pop(Key, None)

    def list_objects_v2(self, *, Bucket, Prefix=""):
        self.call("LIST", Bucket, Prefix)
        return {"Contents": [{"Key": key} for key in self.objects if key.startswith(Prefix)]}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    for name in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "MINIO_ENDPOINT", "MINIO_SECURE",
                 "MINIO_BUCKET", "RECORDING_BUCKET", "BOT_S3_ENDPOINT", "BOT_S3_ACCESS_KEY",
                 "BOT_S3_SECRET_KEY", "BOT_S3_BUCKET", "BOT_USERDATA_S3_PATH", "STORAGE_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MINIO_ACCESS_KEY", "root-key")
    monkeypatch.setenv("MINIO_SECRET_KEY", "root-secret")


@pytest.fixture
def store(monkeypatch):
    fake = FakeS3()
    monkeypatch.setenv("MINIO_ENDPOINT", "storage:9000")
    monkeypatch.setenv("MINIO_BUCKET", "test-recordings")
    monkeypatch.setattr(storage_init, "client", lambda *args: fake)
    return fake


@pytest.mark.parametrize("env,expected", [
    ({}, "http://minio:9000"),
    ({"S3_ENDPOINT": "https://s3.invalid", "MINIO_ENDPOINT": "ignored:9000"}, "https://s3.invalid"),
    ({"S3_ENDPOINT": "", "MINIO_ENDPOINT": "store:9000"}, "http://store:9000"),
    ({"MINIO_ENDPOINT": "store:9000", "MINIO_SECURE": "true"}, "https://store:9000"),
    ({"MINIO_ENDPOINT": "store:9000", "MINIO_SECURE": "TRUE"}, "https://store:9000"),
    ({"MINIO_ENDPOINT": "http://store:9000", "MINIO_SECURE": "true"}, "http://store:9000"),
    ({"MINIO_ENDPOINT": "https://store:9000"}, "https://store:9000"),
    ({"MINIO_ENDPOINT": "store:9000", "MINIO_SECURE": " true "}, "http://store:9000"),
])
def test_endpoint_resolution(monkeypatch, env, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert storage_init.endpoint_meeting_api_uses() == expected


@pytest.mark.parametrize("env,warning_endpoint", [
    ({}, None),
    ({"S3_ENDPOINT": "https://s3.example.com"}, "https://s3.example.com"),
    ({"MINIO_ENDPOINT": "minio:9000"}, "http://minio:9000"),
    ({"S3_ENDPOINT": "http://STORAGE:9000"}, None),
    ({"S3_ENDPOINT": "http://storage:9001"}, "http://storage:9001"),
])
def test_storage_warning_on_every_run(store, monkeypatch, capsys, env, warning_endpoint):
    monkeypatch.setenv("STORAGE_ENDPOINT", "http://storage:9000")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for _ in range(2):
        assert storage_init.main() == 0
        warnings = [line for line in capsys.readouterr().out.splitlines() if "WARNING" in line]
        if warning_endpoint is None:
            assert warnings == []
        else:
            assert warnings == [
                f"[storage-init] WARNING: recordings go to {warning_endpoint} (bucket 'test-recordings'), "
                "not the bundled storage at http://storage:9000. Expected if that is your own S3; "
                f"if this install was upgraded from MinIO, see {storage_init.UPGRADE_DOC}"
            ]


def test_readiness_failure_stops_without_warning(store, monkeypatch, capsys):
    monkeypatch.setenv("S3_ENDPOINT", "https://s3.example.com")
    store.fail = "PUT probe"
    assert storage_init.main() == 1
    output = capsys.readouterr().out
    assert "[storage-init] STOP:" in output
    assert "WARNING" not in output


@pytest.mark.parametrize("endpoint", ["http://storage:9000", "http://minio:9000", "https://s3.invalid"])
def test_unreachable_endpoint_fails_closed(store, monkeypatch, capsys, endpoint):
    monkeypatch.setenv("S3_ENDPOINT", endpoint)
    store.unreachable = True
    assert storage_init.main() == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert all(value in lines[0] for value in (endpoint, "test-recordings", "HEAD bucket", storage_init.UPGRADE_DOC))


@pytest.mark.parametrize("endpoint", ["http://storage:9000", "https://s3.invalid"])
@pytest.mark.parametrize("exists", [True, False])
def test_bucket_and_probe(store, monkeypatch, endpoint, exists):
    monkeypatch.setenv("S3_ENDPOINT", endpoint)
    store.bucket_exists = exists
    original = dict(store.objects)
    assert storage_init.main() == 0
    assert [step for step, _, _ in store.calls] == (
        ["HEAD bucket"] + ([] if exists else ["CREATE bucket"]) + ["PUT probe", "GET probe", "DELETE probe"]
    )
    keys = [key for _, _, key in store.calls if key is not None]
    assert len(set(keys)) == 1
    assert keys[0].startswith(".vexa-storage-init/")
    assert not keys[0].startswith("recordings/")
    assert store.objects == original
    store.calls.clear()
    assert storage_init.main() == 0
    assert next(key for _, _, key in store.calls if key is not None) != keys[0]


@pytest.mark.parametrize("step", ["HEAD bucket", "CREATE bucket", "PUT probe", "GET probe", "DELETE probe"])
def test_failure_names_step_endpoint_and_bucket(store, capsys, step):
    store.fail = step
    store.bucket_exists = step != "CREATE bucket"
    assert storage_init.main() == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert all(value in lines[0] for value in ("http://storage:9000", "test-recordings", step, storage_init.UPGRADE_DOC))


def test_mismatched_bytes_fail_and_probe_is_cleaned_up(store, capsys):
    store.corrupt = True
    original = dict(store.objects)
    assert storage_init.main() == 1
    output = capsys.readouterr().out
    assert all(value in output for value in ("http://storage:9000", "test-recordings", "compare bytes", storage_init.UPGRADE_DOC))
    assert store.objects == original


def test_explicit_credentials_and_bucket_fallback(store, monkeypatch):
    monkeypatch.setenv("S3_ENDPOINT", "https://s3.invalid")
    monkeypatch.setenv("S3_ACCESS_KEY", "explicit-key")
    monkeypatch.setenv("S3_SECRET_KEY", "explicit-secret")
    monkeypatch.delenv("MINIO_BUCKET")
    monkeypatch.setenv("RECORDING_BUCKET", "fallback-bucket")
    clients = []

    def client(*args):
        clients.append(args)
        return store

    monkeypatch.setattr(storage_init, "client", client)
    assert storage_init.main() == 0
    assert clients == [("https://s3.invalid", "explicit-key", "explicit-secret")]
    assert {bucket for _, bucket, _ in store.calls} == {"fallback-bucket"}


def test_scope_probe_never_writes_recordings(store, monkeypatch):
    class ScopedClient:
        def __getattr__(self, operation):
            def call(**kwargs):
                if not kwargs.get("Key", kwargs.get("Prefix", "")).startswith("userdata/"):
                    raise ClientError({"Error": {"Code": "AccessDenied"}}, operation)
                return getattr(store, operation)(**kwargs)
            return call

    monkeypatch.setattr(storage_init, "client", lambda *args: ScopedClient())
    original = dict(store.objects)
    assert storage_init.prove_scope("http://storage:9000", "bot", "secret", "test-recordings", "userdata", store) == []
    assert store.objects == original
    writes = [key for step, _, key in store.calls if step in ("PUT probe", "DELETE probe")]
    assert all(key.startswith((".vexa-storage-init/", "userdata/")) for key in writes)
