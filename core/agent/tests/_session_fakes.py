"""Test doubles for the session store (ADR-0048). Production transcripts go to S3 only."""
from __future__ import annotations


class MemBlobs:
    """The BlobStore port over a dict — immutable keys, like the S3 adapter."""

    def __init__(self):
        self.objects = {}

    def put(self, key, data):
        self.objects[key] = bytes(data)

    def get(self, key):
        return self.objects.get(key)

    def delete(self, key):
        self.objects.pop(key, None)
