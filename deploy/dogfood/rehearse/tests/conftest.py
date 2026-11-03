"""Shared fixtures. Offline by construction: nothing here reads the host's home directory.

The DNA corpus lives at `~/dna-fixtures` on the rig, which is a deployment fact and not a test
input — a suite that read it would pass on bbb and fail everywhere else, and would silently start
measuring whatever somebody left in that directory. `tests/fixtures/` holds two small transcripts
in exactly the corpus's shape instead.
"""
from __future__ import annotations

import pathlib

import pytest

from rehearse import catalogue as cat

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def catalog() -> cat.Catalogue:
    return cat.load()


@pytest.fixture
def env() -> dict:
    """The environment a rehearsal reads: the test domain and the fixture library."""
    return {"VEXA_REHEARSE_DOMAIN": "rehearse.test", "VEXA_DNA_FIXTURES": str(FIXTURES)}


@pytest.fixture
def doors():
    from rehearse.stub_doors import StubDoors
    return StubDoors()
