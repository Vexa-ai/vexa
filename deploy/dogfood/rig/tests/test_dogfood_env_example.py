"""`deploy/dogfood/env.dogfood.example` says what the services it configures actually do.

* Every Google OAuth setting the credential broker declares (`capability: google_oauth`) is in the
  example, and the redirect it carries passes the broker's own boot rule.
* The rig's sign-in switch is described as off by default, with the example's `1` an opt-in.
"""
from __future__ import annotations

import json
import pathlib
import urllib.parse

REPO = pathlib.Path(__file__).resolve().parents[4]
EXAMPLE = REPO / "deploy/dogfood/env.dogfood.example"
BROKER = REPO / "core/agent/services/credential-broker/src/credential_broker"


def _values() -> dict:
    out = {}
    for line in EXAMPLE.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def test_every_broker_google_setting_is_in_the_example():
    declared = {e["key"] for e in json.loads((BROKER / "config.v1.json").read_text())["keys"]
                if e.get("capability") == "google_oauth"}
    assert declared == {"VEXA_CONNECTIONS_GOOGLE_CLIENT_ID", "VEXA_CONNECTIONS_GOOGLE_CLIENT_SECRET",
                        "VEXA_CONNECTIONS_PRODUCT_REDIRECT"}
    assert declared <= set(_values()), sorted(declared - set(_values()))


def test_the_example_redirect_passes_the_broker_boot_rule():
    """The broker refuses to boot unless the redirect is `https://<terminal>/api/auth/callback/google`
    with nothing else on it (`credential_broker/settings.py`), and it is the terminal this file
    names."""
    redirect = _values()["VEXA_CONNECTIONS_PRODUCT_REDIRECT"]
    u = urllib.parse.urlsplit(redirect)
    assert (u.scheme, u.path) == ("https", "/api/auth/callback/google")
    assert u.netloc and not (u.query or u.fragment or u.username or u.password)
    assert redirect == _values()["NEXTAUTH_URL"].rstrip("/") + "/api/auth/callback/google"


def test_the_rig_sign_in_switch_reads_as_off_by_default():
    text = EXAMPLE.read_text()
    block = text[:text.index("VEXA_RIG_OAUTH_ENABLED=")].rsplit("\n\n", 1)[-1]
    assert "OFF BY DEFAULT" in block and "opt-in" in block
    assert "On unless" not in text
