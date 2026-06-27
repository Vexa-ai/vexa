"""Tests for StorageClient.list_objects_bounded — D20 finding-2 protection.

Also covers StorageClient.list_common_prefixes — the delimiter-based
per-recording enumeration the unfinalized-recordings sweep uses to scope chunk
listing to a single session instead of paging a heavy user's entire object list.
"""

import logging
import os

from unittest.mock import MagicMock

import pytest

from meeting_api.storage import LocalStorageClient, MinIOStorageClient


@pytest.fixture
def storage(tmp_path):
    return LocalStorageClient(base_dir=str(tmp_path))


def _seed(storage: LocalStorageClient, count: int, prefix: str = "user1/rec/") -> None:
    base = os.path.join(storage.base_dir, prefix)
    os.makedirs(base, exist_ok=True)
    for i in range(count):
        with open(os.path.join(base, f"chunk-{i:06d}.bin"), "wb") as f:
            f.write(b"x")


def test_returns_all_when_under_cap(storage):
    _seed(storage, 5)
    keys = storage.list_objects_bounded("user1/rec/", max_keys=10)
    assert len(keys) == 5
    assert keys == sorted(keys)


def test_truncates_at_max_keys_and_warns(storage, caplog):
    _seed(storage, 50)
    with caplog.at_level(logging.WARNING, logger="meeting_api.storage"):
        keys = storage.list_objects_bounded("user1/rec/", max_keys=10)
    assert len(keys) == 10
    assert any("truncated at max_keys=10" in rec.message for rec in caplog.records)


def test_empty_prefix_returns_empty(storage):
    assert storage.list_objects_bounded("missing/", max_keys=10) == []


def test_local_list_common_prefixes_returns_immediate_recording_dirs(storage):
    # Layout: recordings/<user>/<rec>/<session>/<media_type>/<chunk>
    for rec in ("735125303957", "999999999999"):
        d = os.path.join(storage.base_dir, "recordings", "1523", rec, "sess", "audio")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "000000.webm"), "wb") as f:
            f.write(b"x")

    prefixes = storage.list_common_prefixes("recordings/1523/")
    assert prefixes == [
        "recordings/1523/735125303957/",
        "recordings/1523/999999999999/",
    ]


def test_local_list_common_prefixes_missing_returns_empty(storage):
    assert storage.list_common_prefixes("recordings/404/") == []


def test_minio_list_common_prefixes_uses_delimiter():
    # Build without __init__ to avoid constructing a real boto3 client.
    client = MinIOStorageClient.__new__(MinIOStorageClient)
    client.bucket = "vexa-recordings"

    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"CommonPrefixes": [
            {"Prefix": "recordings/1523/735125303957/"},
            {"Prefix": "recordings/1523/999999999999/"},
        ]},
        {"CommonPrefixes": [{"Prefix": "recordings/1523/111111111111/"}]},
        {},  # a page with no CommonPrefixes must be tolerated
    ]
    client.client = MagicMock()
    client.client.get_paginator.return_value = paginator

    out = client.list_common_prefixes("recordings/1523/")

    assert out == [
        "recordings/1523/111111111111/",
        "recordings/1523/735125303957/",
        "recordings/1523/999999999999/",
    ]
    paginator.paginate.assert_called_once_with(
        Bucket="vexa-recordings", Prefix="recordings/1523/", Delimiter="/"
    )
