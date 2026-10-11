"""Exercise the migration CLI against paginated, streaming in-memory S3 clients."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody


spec = importlib.util.spec_from_file_location("s3_copy", Path(__file__).parents[1] / "s3_copy.py")
s3_copy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s3_copy)

REPORTS = (
    "changed_in_target_after_copy", "deleted_in_target_after_copy",
    "changed_on_both_sides", "deleted_at_source_after_copy",
)


class FakeS3:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.uploads = []
        self.reads = []
        self.pages = []
        self.fail_uploads = set()
        self.listing_overrides = {}
        self.bucket_exists = True
        self.created_buckets = []
        self.metadata = {}

    def head_bucket(self, *, Bucket):
        if not self.bucket_exists:
            raise ClientError({"Error": {"Code": "NoSuchBucket"}}, "HeadBucket")
        return {}

    def create_bucket(self, *, Bucket):
        self.bucket_exists = True
        self.created_buckets.append(Bucket)
        return {}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "HeadObject")
        data = self.objects[Key]
        return {"ContentLength": len(data), "ETag": '"' + hashlib.md5(data).hexdigest() + '"',
                **self.metadata.get(Key, {})}

    def list_objects_v2(self, *, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        offset = int(ContinuationToken or 0)
        page = keys[offset:offset + 2]
        self.pages.append(page)
        contents = []
        for key in page:
            head = self.head_object(Bucket=Bucket, Key=key)
            item = {"Key": key, "Size": head["ContentLength"], "ETag": head["ETag"]}
            item.update(self.listing_overrides.get(key, {}))
            contents.append(item)
        more = offset + 2 < len(keys)
        return {"Contents": contents, "IsTruncated": more,
                **({"NextContinuationToken": str(offset + 2)} if more else {})}

    def get_object(self, *, Bucket, Key):
        self.head_object(Bucket=Bucket, Key=Key)
        self.reads.append(Key)
        data = self.objects[Key]
        return {"Body": StreamingBody(io.BytesIO(data), len(data))}

    def upload_fileobj(self, Fileobj, Bucket, Key, ExtraArgs):
        self.uploads.append(Key)
        if Key in self.fail_uploads:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        self.objects[Key] = Fileobj.read()
        self.metadata[Key] = ExtraArgs


@pytest.fixture
def copy_run(monkeypatch, tmp_path):
    class CopyRun:
        src = FakeS3({f"recordings/{i}": f"audio-{i}".encode() for i in range(5)})
        dst = FakeS3()
        state = tmp_path / "state"

        def run(self, *args):
            monkeypatch.setattr(sys, "argv", ["s3_copy.py", *args])
            return s3_copy.main()

        def summary(self):
            return json.loads((self.state / "summary.json").read_text())

        def manifest(self):
            return {rec["key"]: rec for rec in map(json.loads, (self.state / "manifest.jsonl").read_text().splitlines())}

        def assert_report(self, name, keys):
            summary = self.summary()
            assert summary["result"] == "verified"
            assert summary["failed"] == []
            assert summary["copied_this_run"] == 0
            for report in REPORTS:
                assert summary[report] == (keys if report == name else [])

    run = CopyRun()
    for prefix in ("SRC", "DST"):
        for suffix, value in {"ENDPOINT": f"http://{prefix.lower()}.invalid:9000",
                              "ACCESS_KEY": "test-access", "SECRET_KEY": "test-secret",
                              "BUCKET": prefix.lower()}.items():
            monkeypatch.setenv(f"{prefix}_{suffix}", value)
    monkeypatch.setenv("STATE_DIR", str(run.state))
    monkeypatch.setattr(s3_copy, "client", lambda prefix: run.src if prefix == "SRC" else run.dst)
    return run


def test_fresh_copy_and_pagination(copy_run, capsys):
    c = copy_run
    c.dst.bucket_exists = False
    c.src.metadata["recordings/0"] = {"ContentType": "audio/wav", "Metadata": {"session": "one"}}
    assert c.run() == 0
    assert c.dst.objects == c.src.objects
    assert c.dst.created_buckets == ["dst"]
    assert c.dst.metadata["recordings/0"] == c.src.metadata["recordings/0"]
    assert c.src.pages == [["recordings/0", "recordings/1"], ["recordings/2", "recordings/3"], ["recordings/4"]]
    assert len(c.dst.uploads) == 5
    manifest = c.manifest()
    for key, data in c.src.objects.items():
        assert manifest[key]["dst_etag"] == hashlib.md5(data).hexdigest()
        assert manifest[key]["dst_size"] == len(data)
        assert manifest[key]["sha256"] == hashlib.sha256(data).hexdigest()
    summary = c.summary()
    assert summary["result"] == "verified"
    assert summary["source"]["objects"] == summary["target"]["objects"] == 5
    assert summary["copied_this_run"] == 5
    assert summary["copied_bytes_this_run"] == sum(map(len, c.src.objects.values()))
    assert summary["adopted"] == 0
    assert not (c.state / "COMPLETE").exists()
    assert all(summary[name] == [] for name in REPORTS)
    output = capsys.readouterr().out
    line = next(line for line in output.splitlines() if line.startswith("COPY-SUMMARY "))
    stdout_summary = json.loads(line.removeprefix("COPY-SUMMARY "))
    for key in ("result", "source", "target", "copied_this_run", "already_verified", "seconds"):
        assert stdout_summary[key] == summary[key]


@pytest.mark.parametrize("args", [(), ("--reverify",)])
@pytest.mark.parametrize("new_data", [b"edited!", b"a longer live recording", None])
def test_target_changes_are_preserved(copy_run, new_data, args, capsys):
    c, key = copy_run, "recordings/0"
    assert c.run() == 0
    original = c.manifest()[key]
    if new_data is None:
        del c.dst.objects[key]
        report = "deleted_in_target_after_copy"
    else:
        c.dst.objects[key] = new_data
        report = "changed_in_target_after_copy"
    c.dst.reads.clear()
    capsys.readouterr()
    assert c.run(*args) == 0
    assert c.dst.objects.get(key) == new_data
    assert c.dst.reads.count(key) == int(new_data is not None and len(new_data) == original["dst_size"])
    assert len(c.dst.uploads) == 5
    assert c.manifest()[key] == original
    c.assert_report(report, [key])
    output = capsys.readouterr().out
    assert f"{report}: 1" in output
    assert "1 changed or deleted in the new storage" in output


@pytest.mark.parametrize("args", [(), ("--reverify",)])
@pytest.mark.parametrize("same_bytes", [True, False])
def test_changed_etag_is_checked_against_verified_bytes(copy_run, args, same_bytes):
    c, key = copy_run, "recordings/0"
    assert c.run() == 0
    original = c.manifest()[key]
    manifest_path = c.state / "manifest.jsonl"
    before = manifest_path.read_text()
    if not same_bytes:
        c.dst.objects[key] = b"edited!"
    c.dst.listing_overrides[key] = {"ETag": '"new-etag"'}
    c.src.reads.clear()
    c.dst.reads.clear()

    assert c.run(*args) == 0
    assert c.dst.reads.count(key) == 1
    assert c.src.reads == []
    assert len(c.dst.uploads) == 5
    assert c.summary()["copied_bytes_this_run"] == c.summary()["adopted"] == 0
    c.assert_report("changed_in_target_after_copy", [] if same_bytes else [key])
    if same_bytes:
        corrected = {**original, "dst_etag": "new-etag"}
        assert c.manifest()[key] == corrected
        assert manifest_path.read_text() == before + json.dumps(corrected) + "\n"
        c.dst.reads.clear()
        assert c.run() == 0
        assert c.dst.reads == []
        assert manifest_path.read_text() == before + json.dumps(corrected) + "\n"
    else:
        assert manifest_path.read_text() == before
        assert c.dst.objects[key] == b"edited!"


@pytest.mark.parametrize("new_data", [b"source!", b"new source recording"])
def test_source_changed_target_untouched(copy_run, new_data):
    c, key = copy_run, "recordings/0"
    assert c.run() == 0
    c.src.objects[key] = new_data
    assert c.run() == 0
    assert c.dst.objects == c.src.objects
    assert c.dst.uploads == [f"recordings/{i}" for i in range(5)] + [key]
    assert c.summary()["copied_this_run"] == 1
    assert c.summary()["result"] == "verified"
    assert all(c.summary()[name] == [] for name in REPORTS)
    assert c.manifest()[key]["dst_etag"] == hashlib.md5(new_data).hexdigest()


@pytest.mark.parametrize("new_data", [b"target!", None])
def test_both_sides_changed(copy_run, new_data):
    c, key = copy_run, "recordings/0"
    assert c.run() == 0
    c.src.objects[key] = b"source!"
    if new_data is None:
        del c.dst.objects[key]
    else:
        c.dst.objects[key] = new_data
    assert c.run() == 0
    assert c.dst.objects.get(key) == new_data
    assert len(c.dst.uploads) == 5
    c.assert_report("changed_on_both_sides", [key])


def test_deleted_at_source_is_reported_without_deleting_target(copy_run):
    c, key = copy_run, "recordings/0"
    assert c.run() == 0
    old_target = dict(c.dst.objects)
    del c.src.objects[key]
    assert c.run() == 0
    assert c.dst.objects == old_target
    assert len(c.dst.uploads) == 5
    c.assert_report("deleted_at_source_after_copy", [key])


@pytest.mark.parametrize("same", [True, False])
def test_existing_untracked_target_is_adopted_or_reported(copy_run, same):
    c, key = copy_run, "recordings/0"
    c.src.objects = {key: b"source!"}
    c.dst.objects[key] = b"source!" if same else b"target!"
    old_target = dict(c.dst.objects)
    assert c.run() == 0
    assert c.dst.objects == old_target
    assert c.dst.uploads == []
    assert key in c.src.reads and key in c.dst.reads
    summary = c.summary()
    assert summary["copied_this_run"] == summary["copied_bytes_this_run"] == 0
    assert summary["adopted"] == int(same)
    if same:
        assert c.manifest()[key]["dst_etag"] == hashlib.md5(b"source!").hexdigest()
        assert c.manifest()[key]["dst_size"] == 7
        assert summary["result"] == "verified"
        assert all(summary[name] == [] for name in REPORTS)
    else:
        assert key not in c.manifest()
        c.assert_report("changed_on_both_sides", [key])


def test_reverify_detects_corruption_with_unchanged_listing(copy_run):
    c, key = copy_run, "recordings/0"
    assert c.run() == 0
    rec = c.manifest()[key]
    c.dst.objects[key] = b"corrupt"
    c.dst.listing_overrides[key] = {"Size": rec["dst_size"], "ETag": rec["dst_etag"]}
    assert c.run("--reverify") == 1
    assert c.dst.objects[key] == b"corrupt"
    assert len(c.dst.uploads) == 5
    assert c.summary()["result"] == "incomplete"
    assert [item["key"] for item in c.summary()["failed"]] == [key]
    assert not (c.state / "COMPLETE").exists()


def test_reverify_reads_only_target_without_uploading(copy_run):
    c = copy_run
    assert c.run() == 0
    c.src.reads.clear()
    c.dst.reads.clear()
    assert c.run("--reverify") == 0
    assert c.src.reads == []
    assert sorted(c.dst.reads) == sorted(c.src.objects)
    assert c.dst.objects == c.src.objects
    assert len(c.dst.uploads) == 5
    assert c.summary()["result"] == "verified"
    assert c.summary()["copied_this_run"] == 0


def test_upload_failure_is_incomplete_and_retry_recovers(copy_run):
    c, key = copy_run, "recordings/2"
    c.dst.fail_uploads.add(key)
    assert c.run() == 1
    assert key not in c.dst.objects
    assert c.summary()["result"] == "incomplete"
    assert c.summary()["failed"] == [{"key": key, "error": "AccessDenied"}]
    assert c.summary()["copied_this_run"] == 4
    assert c.summary()["missing_in_target"] == [key]
    assert c.summary()["not_hash_verified"] == [key]
    c.dst.fail_uploads.clear()
    assert c.run() == 0
    assert c.dst.objects == c.src.objects
    assert c.summary()["copied_this_run"] == 1
    assert c.summary()["failed"] == []


def test_dry_run_does_not_upload_or_write_verification_state(copy_run):
    c = copy_run
    assert c.run("--dry-run") == 0
    assert c.dst.uploads == []
    assert c.dst.objects == {}
    assert not (c.state / "manifest.jsonl").exists()
    assert not (c.state / "summary.json").exists()
    assert not (c.state / "COMPLETE").exists()


@pytest.mark.parametrize("source_changed", [True, False])
def test_unknown_recorded_target_state_never_overwrites(copy_run, source_changed):
    c, key = copy_run, "recordings/0"
    assert c.run() == 0
    records = c.manifest()
    del records[key]["dst_etag"]
    (c.state / "manifest.jsonl").write_text("".join(json.dumps(rec) + "\n" for rec in records.values()))
    old_target = dict(c.dst.objects)
    if source_changed:
        c.src.objects[key] = b"changed"
    assert c.run("--reverify") == 0
    assert c.dst.objects == old_target
    assert len(c.dst.uploads) == 5
    c.assert_report("changed_on_both_sides" if source_changed else "changed_in_target_after_copy", [key])


def test_prefix_limits_source_deletion_reports(copy_run):
    c = copy_run
    c.src.objects = {"inside/a": b"a", "inside/b": b"b", "outside/c": b"c"}
    assert c.run() == 0
    old_target = dict(c.dst.objects)
    del c.src.objects["inside/a"]
    del c.src.objects["outside/c"]
    assert c.run("--prefix", "inside/") == 0
    assert c.dst.objects == old_target
    c.assert_report("deleted_at_source_after_copy", ["inside/a"])
    assert c.summary()["source"]["objects"] == 1


def test_report_cap_does_not_affect_final_verdict(copy_run, capsys):
    c = copy_run
    c.src.objects = {f"key/{i:04d}": b"source" for i in range(1002)}
    c.dst.objects = {key: b"target" for key in c.src.objects}
    assert c.run() == 0
    assert c.dst.uploads == []
    assert set(c.dst.objects.values()) == {b"target"}
    assert c.summary()["result"] == "verified"
    assert len(c.summary()["changed_on_both_sides"]) == 1000
    assert "changed_on_both_sides: 1002" in capsys.readouterr().out
