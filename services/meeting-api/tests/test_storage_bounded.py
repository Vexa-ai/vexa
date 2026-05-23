"""Tests for StorageClient.list_objects_bounded — D20 finding-2 protection."""

import logging
import os

import pytest

from meeting_api.storage import LocalStorageClient


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
