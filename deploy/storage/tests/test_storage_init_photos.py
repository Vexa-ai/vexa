"""People's photos: admin-api has its own storage account, scoped to ``person-photos/``.

storage-init makes the account on the bundled storage, grants it list/read/write/delete under the
photos prefix of the recordings bucket and nothing else, and proves the limit before the stack starts.
versitygw keeps one policy per bucket, so when the bots' userdata prefix lives in the same bucket both
grants go into that one policy. Offline: a store that evaluates the attached policy the way an S3
bucket policy does (resource ARN globs, the ListBucket prefix condition)."""
import importlib.util
import io
import json
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

spec = importlib.util.spec_from_file_location("storage_init_p", Path(__file__).parents[1] / "storage_init.py")
storage_init = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage_init)

ROOT = ("root-key", "root-secret")
PHOTOS = ("vexa-photos-key", "photos-secret")
BOT = ("bot-key", "bot-secret")


class Store:
    def __init__(self):
        self.buckets = {"vexa": {"recordings/1.wav": b"rec", "person-photos/7/upload-0123456789abcdef.png": b"old photo"},
                        "vexa-sessions": {}}
        self.policies: dict[str, str] = {}
        self.policy_puts: list[str] = []

    def allowed(self, ak, bucket, action, key=None, prefix=None):
        if ak == ROOT[0]:
            return True
        pol = json.loads(self.policies.get(bucket, '{"Statement": []}'))
        for st in pol["Statement"]:
            if st["Effect"] != "Allow" or ak not in st["Principal"]["AWS"] or action not in st["Action"]:
                continue
            target = f"arn:aws:s3:::{bucket}" + (f"/{key}" if key is not None else "")
            if not any(fnmatchcase(target, r) for r in st["Resource"]):
                continue
            cond = (st.get("Condition") or {}).get("StringLike", {}).get("s3:prefix")
            if cond and not any(fnmatchcase(prefix or "", c) for c in cond):
                continue
            return True
        return False


class Client:
    def __init__(self, store, ak, leaky=False):
        self.s, self.ak, self.leaky = store, ak, leaky

    def _check(self, bucket, action, key=None, prefix=None):
        if not (self.leaky or self.s.allowed(self.ak, bucket, action, key, prefix)):
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
        self.s.policy_puts.append(Bucket)

    def put_object(self, *, Bucket, Key, Body):
        self._check(Bucket, "s3:PutObject", Key)
        self.s.buckets.setdefault(Bucket, {})[Key] = Body

    def get_object(self, *, Bucket, Key):
        self._check(Bucket, "s3:GetObject", Key)
        if Key not in self.s.buckets.get(Bucket, {}):
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.s.buckets[Bucket][Key])}

    def delete_object(self, *, Bucket, Key):
        self._check(Bucket, "s3:DeleteObject", Key)
        self.s.buckets.get(Bucket, {}).pop(Key, None)

    def delete_objects(self, *, Bucket, Delete):
        errors = []
        for o in Delete["Objects"]:
            try:
                self.delete_object(Bucket=Bucket, Key=o["Key"])
            except ClientError:
                errors.append({"Key": o["Key"], "Code": "AccessDenied"})
        return {"Errors": errors} if errors else {}

    def list_objects_v2(self, *, Bucket, Prefix=""):
        self._check(Bucket, "s3:ListBucket", prefix=Prefix)
        return {"Contents": [{"Key": k} for k in self.s.buckets.get(Bucket, {}) if k.startswith(Prefix)]}


@pytest.fixture
def env(monkeypatch):
    for name in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "MINIO_SECURE", "RECORDING_BUCKET",
                 "BOT_S3_ENDPOINT", "BOT_S3_ACCESS_KEY", "BOT_S3_SECRET_KEY", "BOT_S3_BUCKET",
                 "BOT_USERDATA_S3_PATH", "VEXA_SESSIONS_S3_ACCESS_KEY", "VEXA_SESSIONS_S3_SECRET_KEY",
                 "VEXA_SESSIONS_S3_ENDPOINT", "VEXA_SESSIONS_S3_BUCKET", "VEXA_SESSIONS_S3_OPERATOR_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STORAGE_ENDPOINT", "http://storage:9000")
    monkeypatch.setenv("MINIO_ENDPOINT", "storage:9000")
    monkeypatch.setenv("MINIO_BUCKET", "vexa")
    monkeypatch.setenv("MINIO_ACCESS_KEY", ROOT[0])
    monkeypatch.setenv("MINIO_SECRET_KEY", ROOT[1])
    monkeypatch.setenv("VEXA_PHOTOS_S3_ACCESS_KEY", PHOTOS[0])
    monkeypatch.setenv("VEXA_PHOTOS_S3_SECRET_KEY", PHOTOS[1])
    monkeypatch.setattr(storage_init.time, "sleep", lambda s: None)
    store = Store()
    accounts = []
    monkeypatch.setattr(storage_init, "ensure_scoped_account", lambda ep, rak, rsk, ak, sk: accounts.append(ak) or "created")
    store.accounts = accounts
    store.leaky = set()
    monkeypatch.setattr(storage_init, "client", lambda ep, ak, sk: Client(store, ak, leaky=ak in store.leaky))
    return store


def test_the_photos_policy_reaches_only_the_photos_prefix():
    sts = storage_init.photos_statements("vexa", "pk")
    actions = {a for st in sts for a in st["Action"]}
    assert actions == {"s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"}
    assert all(st["Principal"] == {"AWS": ["pk"]} and st["Effect"] == "Allow" for st in sts)
    for st in sts:
        if st["Action"] == ["s3:ListBucket"]:
            assert st["Resource"] == ["arn:aws:s3:::vexa"]
            assert st["Condition"] == {"StringLike": {"s3:prefix": ["person-photos/*"]}}
        else:
            assert st["Resource"] == ["arn:aws:s3:::vexa/person-photos/*"]


def test_the_prefix_is_admin_apis():
    admin = (Path(__file__).parents[3] / "core/identity/services/admin-api/src/admin_api/app/photo_storage.py").read_text()
    assert f'PREFIX = "{storage_init.PHOTOS_PREFIX}/"' in admin


def test_main_makes_the_photos_account_and_proves_it(env, capsys):
    assert storage_init.main() == 0
    out = capsys.readouterr().out
    assert env.accounts == [PHOTOS[0]]
    assert "people's photos account scoped to s3://vexa/person-photos/" in out
    pol = json.loads(env.policies["vexa"])
    assert {p for st in pol["Statement"] for p in st["Principal"]["AWS"]} == {PHOTOS[0]}
    # an existing photo stays where it was and the photos key reads it
    c = Client(env, PHOTOS[0])
    assert c.get_object(Bucket="vexa", Key="person-photos/7/upload-0123456789abcdef.png")["Body"].read() == b"old photo"
    with pytest.raises(ClientError):
        c.get_object(Bucket="vexa", Key="recordings/1.wav")
    assert env.buckets["vexa"]["recordings/1.wav"] == b"rec"
    assert not [k for b in env.buckets.values() for k in b if ".vexa-storage-init" in k]


def test_one_policy_holds_both_grants_when_bots_share_the_bucket(env, monkeypatch):
    monkeypatch.setenv("BOT_S3_ENDPOINT", "http://storage:9000")
    monkeypatch.setenv("BOT_S3_ACCESS_KEY", BOT[0])
    monkeypatch.setenv("BOT_S3_SECRET_KEY", BOT[1])
    monkeypatch.setenv("BOT_USERDATA_S3_PATH", "userdata")
    assert storage_init.main() == 0
    assert env.policy_puts == ["vexa"]
    pol = json.loads(env.policies["vexa"])
    assert {p for st in pol["Statement"] for p in st["Principal"]["AWS"]} == {PHOTOS[0], BOT[0]}
    assert sorted(env.accounts) == sorted([PHOTOS[0], BOT[0]])


def test_main_stops_when_the_photos_key_reaches_recordings(env, capsys):
    env.leaky.add(PHOTOS[0])
    assert storage_init.main() == 1
    out = capsys.readouterr().out
    assert "STOP" in out and "read in s3://vexa/recordings/ was allowed but must be denied" in out
    assert env.buckets["vexa"]["recordings/1.wav"] == b"rec"


def test_the_proof_covers_the_session_store_and_bot_userdata(env, monkeypatch):
    env.policies["vexa"] = json.dumps(storage_init.bucket_policy(storage_init.photos_statements("vexa", PHOTOS[0])))
    sessions_leak = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"AWS": [PHOTOS[0]]}, "Action": ["s3:GetObject"],
         "Resource": ["arn:aws:s3:::vexa-sessions/*"]}]}
    env.policies["vexa-sessions"] = json.dumps(sessions_leak)
    root = Client(env, ROOT[0])
    bad = storage_init.prove_photos_scope("http://storage:9000", *PHOTOS, "vexa", root,
                                          [("vexa", "userdata"), ("vexa-sessions", "")])
    assert bad == ["read in s3://vexa-sessions/ was allowed but must be denied"]


@pytest.mark.parametrize("photos_var,root_var,root_val", [
    ("VEXA_PHOTOS_S3_ACCESS_KEY", "MINIO_ACCESS_KEY", ROOT[0]),
    ("VEXA_PHOTOS_S3_ACCESS_KEY", "S3_ACCESS_KEY", "recordings-key"),
    ("VEXA_PHOTOS_S3_SECRET_KEY", "MINIO_SECRET_KEY", ROOT[1]),
    ("VEXA_PHOTOS_S3_SECRET_KEY", "S3_SECRET_KEY", "recordings-secret"),
])
def test_main_refuses_a_photos_pair_that_reuses_a_root_half(env, monkeypatch, capsys, photos_var, root_var, root_val):
    monkeypatch.setenv(root_var, root_val)
    monkeypatch.setenv(photos_var, root_val)
    env.leaky.add("recordings-key")  # an S3_* pair is the recordings key: the readiness probe passes
    assert storage_init.main() == 1
    out = capsys.readouterr().out
    assert f"STOP: {photos_var}" in out and root_val not in out
    assert env.accounts == [] and env.policies == {}


def test_a_bot_prefix_inside_the_photos_prefix_is_refused(env, monkeypatch, capsys):
    monkeypatch.setenv("BOT_S3_ENDPOINT", "http://storage:9000")
    monkeypatch.setenv("BOT_S3_ACCESS_KEY", BOT[0])
    monkeypatch.setenv("BOT_S3_SECRET_KEY", BOT[1])
    monkeypatch.setenv("BOT_USERDATA_S3_PATH", "person-photos/bots")
    assert storage_init.main() == 1
    assert "BOT_USERDATA_S3_PATH is under person-photos/" in capsys.readouterr().out


def test_no_photos_key_means_no_account(env, monkeypatch, capsys):
    monkeypatch.delenv("VEXA_PHOTOS_S3_ACCESS_KEY")
    assert storage_init.main() == 0
    assert env.accounts == [] and env.policies == {}
    assert "admin-api shows initials" in capsys.readouterr().out


def test_an_operator_s3_proves_io_under_the_prefix_only(env, monkeypatch, capsys):
    monkeypatch.setenv("S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setenv("S3_ACCESS_KEY", "recordings-key")
    monkeypatch.setenv("S3_SECRET_KEY", "recordings-secret")
    env.policies["vexa"] = json.dumps(storage_init.bucket_policy(storage_init.photos_statements("vexa", PHOTOS[0])))
    env.leaky.add("recordings-key")
    assert storage_init.main() == 0
    out = capsys.readouterr().out
    assert "photos key: PUT/GET/DELETE verified under s3://vexa/person-photos/" in out
    assert env.accounts == [] and env.policy_puts == []
