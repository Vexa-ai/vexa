"""The credential broker's state backup on compose (offline — no stack, no docker).

`credential-broker-backup` snapshots the broker's state volume into its own volume on a schedule,
on by default. It mounts the state and the backup volume and nothing else: no key volume (the store
key above all), no network. The broker itself never mounts the backup volume.
"""
from __future__ import annotations

import re

from workspace_store_wiring_test import COMPOSE, _service


def _mounts(block: str) -> dict:
    return {target: source for source, target in
            re.findall(r"^\s+- ([A-Za-z0-9_.-]+):(/\S+?)(?::ro)?$", block, flags=re.M)}


def test_the_backup_snapshots_the_state_into_its_own_volume():
    block = _service("credential-broker-backup")
    assert _mounts(block) == {"/state": "connections-state", "/backup": "connections-backup"}
    assert '"credential_broker.backup"' in block and '"--dest", "/backup"' in block
    assert "${VEXA_CONNECTIONS_BACKUP_KEEP:-28}" in block
    assert "${VEXA_CONNECTIONS_BACKUP_INTERVAL_S:-21600}" in block
    assert "network_mode: none" in block and "read_only: true" in block
    assert "profiles:" not in block, "the backup is on by default"
    assert re.search(r"^  connections-backup:$", COMPOSE.read_text(), flags=re.M)


def test_no_key_reaches_the_backup():
    block = _service("credential-broker-backup")
    assert "key" not in " ".join(_mounts(block).values())
    assert "/run/vexa-connections" not in block and "STORE_KEY" not in block


def test_the_broker_and_the_key_holders_never_mount_the_backup_volume():
    text = COMPOSE.read_text()
    users = {m for m in re.findall(r"^  ([a-z0-9-]+):$", text, flags=re.M)
             if "connections-backup:/" in _service(m)}
    assert users == {"credential-broker-backup"}, users
    assert "connections-backup" not in _service("connections-keys")
