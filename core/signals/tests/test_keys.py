import json

import httpx
import pytest

from meeting_signals import broker_assertion
from meeting_signals.keys import KeySource, KeyUnavailable
from conftest import run

ROLE_KEY = "signals-role-key-" + "0123456789abcdef" * 3


@pytest.fixture
def key_file(tmp_path):
    p = tmp_path / "signals.key"
    p.write_text(ROLE_KEY + "\n")
    return str(p)


def test_none_is_a_keyless_endpoint():
    assert run(KeySource().resolve("none")) is None


def test_the_key_comes_from_the_broker_with_the_signals_role(key_file):
    seen = []

    def handler(req):
        claims = broker_assertion.verify(req.headers[broker_assertion.HEADER], key_for=lambda r: ROLE_KEY.encode(),
                                         method="POST", path=req.url.path, body=req.content)
        seen.append(claims)
        assert json.loads(req.content) == {"name": "openrouter"}
        return httpx.Response(200, json={"name": "openrouter", "value": "MODEL-KEY"})

    ks = KeySource("http://broker", key_file, env={}, transport=httpx.MockTransport(handler))
    assert run(ks.resolve("broker:signals/openrouter")) == "MODEL-KEY"
    assert run(ks.resolve("broker:signals/openrouter")) == "MODEL-KEY"      # cached
    assert len(seen) == 1 and seen[0]["role"] == "signals" and seen[0]["actor"] == "signals"


def test_the_environment_is_never_read_outside_dev(key_file):
    env = {"SIGNALS_OPENROUTER_API_KEY": "ENV-KEY", "VEXA_ENV": "prod"}
    with pytest.raises(KeyUnavailable) as e:
        run(KeySource(env=env).resolve("broker:signals/openrouter"))
    assert "ENV-KEY" not in str(e.value)
    missing = httpx.MockTransport(lambda r: httpx.Response(404))
    with pytest.raises(KeyUnavailable):
        run(KeySource("http://broker", key_file, env=env, transport=missing).resolve("broker:signals/openrouter"))


def test_dev_fallback_only_with_vexa_env_dev(key_file):
    env = {"SIGNALS_OPENROUTER_API_KEY": "ENV-KEY", "VEXA_ENV": "dev"}
    assert run(KeySource(env=env).resolve("broker:signals/openrouter")) == "ENV-KEY"
    missing = httpx.MockTransport(lambda r: httpx.Response(404))
    assert run(KeySource("http://broker", key_file, env=env, transport=missing).resolve("broker:signals/x")) == "ENV-KEY"


def test_a_broker_refusal_is_named_without_a_value(key_file):
    t = httpx.MockTransport(lambda r: httpx.Response(403))
    with pytest.raises(KeyUnavailable) as e:
        run(KeySource("http://broker", key_file, env={"VEXA_ENV": "dev"}, transport=t).resolve("broker:signals/x"))
    assert "403" in str(e.value)
