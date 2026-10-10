"""Settings → Models `default_model` (ADR-0043): a model-catalog id, shape-checked at write time.

The catalog is agent-api's (VEXA_MODEL_CATALOG), so identity never checks the id against a list —
only that it is shaped like one (models.v1 ModelId, held equal by the `model-catalog-id` fact in
scripts/parity.json). L1: the rulebook alone, no database.
"""
import pytest
from fastapi import HTTPException

from admin_api.app.platform_settings import (MODELS_FIELDS, SETTINGS, apply_config_update,
                                             validate_config_fields)


def test_default_model_is_a_models_field_on_both_tiers():
    assert "default_model" in MODELS_FIELDS
    assert "default_model" in SETTINGS["models"].fields


@pytest.mark.parametrize("value", ["qwen3-32b", "or-sonnet", "claude.4_5"])
def test_a_catalog_id_is_stored(value):
    cleaned = validate_config_fields({"default_model": value})
    assert apply_config_update({}, cleaned) == {"default_model": value}


@pytest.mark.parametrize("value", ["Not An Id", "-leading", "a/b", "x" * 65, "Qwen/Qwen3-32B"])
def test_anything_not_shaped_like_a_catalog_id_is_refused(value):
    with pytest.raises(HTTPException) as exc:
        validate_config_fields({"default_model": value})
    assert exc.value.status_code == 422


def test_an_empty_default_clears_it():
    assert apply_config_update({"default_model": "claude"},
                               validate_config_fields({"default_model": ""})) == {}
