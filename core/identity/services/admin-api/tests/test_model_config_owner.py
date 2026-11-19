"""Settings → Models: a person's own endpoint carries the person's own credential (no database).

`_effective_models` is the resolution `/internal/users/{id}/model-config` serves the dispatch: user
over platform, field by field, except that naming your own `base_url` makes you the owner of that
endpoint's credential and request fields. The database-backed twin is
`test_model_settings.py::test_a_persons_own_endpoint_never_inherits_the_platform_credential`.
"""
from admin_api.app.main import _effective_models

PLATFORM = {"mode": "custom", "base_url": "https://gw.operator.example/v1", "api_key": "operator-key",
            "extra_body": '{"operator": true}', "model": "operator-model", "effort": "medium"}


def test_an_own_endpoint_takes_nothing_endpoint_bound_from_the_platform():
    out = _effective_models({"base_url": "https://own.example/v1"}, PLATFORM)
    assert out["base_url"] == "https://own.example/v1"
    assert "api_key" not in out and "extra_body" not in out
    assert out["model"] == "operator-model" and out["effort"] == "medium"   # not endpoint-bound


def test_an_own_endpoint_with_its_own_key_keeps_it():
    out = _effective_models({"base_url": "https://own.example/v1", "api_key": "mine",
                             "extra_body": '{"mine": 1}'}, PLATFORM)
    assert (out["api_key"], out["extra_body"]) == ("mine", '{"mine": 1}')


def test_without_an_own_endpoint_the_merge_is_unchanged():
    assert _effective_models({"model": "mine"}, PLATFORM) == {**PLATFORM, "model": "mine"}
    assert _effective_models({}, {}) == {}
