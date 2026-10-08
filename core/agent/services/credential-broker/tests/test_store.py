"""The store port: the local AES-256-GCM adapter (versions, CAS, integrity, no plaintext at rest)
and the OpenBao KV v2 adapter (path layout, CAS, missing, typed faults)."""
import json
import os
import sqlite3

import httpx
import pytest

from credential_broker.store import LocalEncryptedStore, OpenBaoStore, StoreUnavailable, load_store_key

KEY = bytes(range(32))


@pytest.fixture
def local(tmp_path):
    return LocalEncryptedStore(tmp_path / "secrets.sqlite", KEY)


def test_versions_round_trip_and_latest(local):
    assert local.get("abc") is None
    a = local.put("abc", {"value": "one"})
    b = local.put("abc", {"value": "two"})
    assert (a.version, b.version) == (1, 2) and a.receipt != b.receipt
    assert local.get("abc").data == {"value": "two"}
    assert local.get("abc", version=1).data == {"value": "one"}
    assert local.get("abc", version=9) is None


def test_cas_refuses_a_racing_writer(local):
    local.put("git/pat/2", {"value": "a"}, cas=0)
    with pytest.raises(StoreUnavailable) as e:
        local.put("git/pat/2", {"value": "b"}, cas=0)
    assert e.value.kind == "conflict"
    assert local.put("git/pat/2", {"value": "b"}, cas=1).version == 2


def test_no_plaintext_at_rest(tmp_path, local):
    local.put("abc", {"value": "PLAINTEXT-CANARY-VALUE"})
    assert b"PLAINTEXT-CANARY-VALUE" not in (tmp_path / "secrets.sqlite").read_bytes()


def test_wrong_key_and_tampering_are_integrity_faults_not_absence(tmp_path, local):
    local.put("abc", {"value": "x"})
    with pytest.raises(StoreUnavailable) as e:
        LocalEncryptedStore(tmp_path / "secrets.sqlite", bytes(32)).get("abc")
    assert e.value.kind == "integrity"
    with sqlite3.connect(tmp_path / "secrets.sqlite") as c:
        c.execute("UPDATE secret_versions SET path='other' WHERE path='abc'")
    with pytest.raises(StoreUnavailable):
        local.get("other")          # a row moved under another name does not decrypt


@pytest.mark.parametrize("path", ["", "../x", "a/../b", "a//b", "a b", "x" * 200])
def test_paths_are_validated(local, path):
    with pytest.raises(StoreUnavailable):
        local.put(path, {})


def test_store_key_file(tmp_path):
    p = tmp_path / "k"
    p.write_text(KEY.hex() + "\n")
    assert load_store_key(p) == KEY
    for bad in ["short", "zz" * 32, KEY.hex()[:-2]]:
        p.write_text(bad)
        with pytest.raises(StoreUnavailable):
            load_store_key(p)
    with pytest.raises(StoreUnavailable):
        load_store_key(tmp_path / "missing")


def openbao(tmp_path, handler):
    token = tmp_path / "token"
    token.write_text("fixture-openbao-token\n")
    return OpenBaoStore("http://bao:8200", token, transport=httpx.MockTransport(handler))


def test_openbao_kv2_layout_and_versions(tmp_path):
    seen = []

    def handler(req):
        seen.append(req)
        assert req.headers["x-vault-token"] == "fixture-openbao-token"
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["options"] == {"cas": 0}
            return httpx.Response(200, json={"request_id": "r1", "data": {"version": 1}})
        if req.url.path.endswith("/missing"):
            return httpx.Response(404, json={"errors": []})
        assert req.url.params["version"] == "1"
        return httpx.Response(200, json={"request_id": "r2", "data": {"data": {"value": "v"}, "metadata": {"version": 1}}})
    s = openbao(tmp_path, handler)
    assert s.put("git/pat/2", {"value": "v"}, cas=0).version == 1
    assert seen[0].url.path == "/v1/connections/data/git/pat/2"
    r = s.get("git/pat/2", version=1)
    assert r.data == {"value": "v"} and r.receipt == "r2"
    assert s.get("missing") is None


@pytest.mark.parametrize("status,kind", [(400, "conflict"), (403, "config"), (500, "transport")])
def test_openbao_faults_are_typed(tmp_path, status, kind):
    s = openbao(tmp_path, lambda req: httpx.Response(status, json={"errors": ["PRIVATE-DETAIL"]}))
    with pytest.raises(StoreUnavailable) as e:
        s.put("abc", {"value": "x"}, cas=1)
    assert e.value.kind == kind and "PRIVATE" not in str(e.value)


def test_openbao_unreachable_is_transport(tmp_path):
    def down(req):
        raise httpx.ConnectError("down")
    with pytest.raises(StoreUnavailable) as e:
        openbao(tmp_path, down).get("abc")
    assert e.value.kind == "transport"


def test_openbao_config_refusals(tmp_path):
    with pytest.raises(StoreUnavailable):
        OpenBaoStore("bao:8200", tmp_path / "t")
    with pytest.raises(StoreUnavailable) as e:
        OpenBaoStore("http://bao:8200", tmp_path / "absent", transport=httpx.MockTransport(lambda r: httpx.Response(200))).get("abc")
    assert e.value.kind == "config"
