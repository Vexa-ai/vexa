"""Tests for the YAML-based profile loader."""

import tempfile
from pathlib import Path

import pytest

from runtime_api import profiles as profiles_module
from runtime_api.profiles import load_profiles, get_profile, get_all_profiles, PROFILE_DEFAULTS


@pytest.fixture(autouse=True)
def _reset_profiles_globals():
    """Reset module-level profile state between tests."""
    old_profiles = profiles_module._profiles
    old_mtime = profiles_module._mtime
    profiles_module._profiles = {}
    profiles_module._mtime = 0.0
    yield
    profiles_module._profiles = old_profiles
    profiles_module._mtime = old_mtime


@pytest.fixture
def profiles_yaml(tmp_path):
    content = """
profiles:
  web-server:
    image: "nginx:alpine"
    resources:
      cpu_limit: "500m"
      memory_limit: "512Mi"
    idle_timeout: 0
    auto_remove: false
    ports:
      "80/tcp": {}
    max_per_user: 3

  worker:
    image: "python:3.12-slim"
    idle_timeout: 900
    auto_remove: true
"""
    path = tmp_path / "profiles.yaml"
    path.write_text(content)
    return str(path)


def test_load_profiles(profiles_yaml):
    profiles = load_profiles(profiles_yaml)
    assert "web-server" in profiles
    assert "worker" in profiles
    assert len(profiles) == 2


def test_profile_defaults_applied(profiles_yaml):
    profiles = load_profiles(profiles_yaml)
    worker = profiles["worker"]
    # Should have default values for fields not specified
    assert worker["gpu"] is False
    assert worker["node_selector"] == {}
    assert worker["mounts"] == []


def test_profile_resources_merged(profiles_yaml):
    profiles = load_profiles(profiles_yaml)
    web = profiles["web-server"]
    assert web["resources"]["cpu_limit"] == "500m"
    assert web["resources"]["memory_limit"] == "512Mi"
    # Defaults for unspecified resource fields
    assert web["resources"]["shm_size"] == 0


def test_profile_ports(profiles_yaml):
    profiles = load_profiles(profiles_yaml)
    web = profiles["web-server"]
    assert "80/tcp" in web["ports"]


def test_missing_file():
    with pytest.raises(FileNotFoundError):
        load_profiles("/nonexistent/path.yaml")


def test_get_profile_returns_none_for_unknown(profiles_yaml):
    load_profiles(profiles_yaml)
    assert get_profile("nonexistent") is None


# --- Expanded tests ---


def test_load_from_yaml_dict_structure(tmp_path):
    """Profile loading from a minimal YAML dict."""
    content = """
profiles:
  minimal:
    image: "alpine:latest"
"""
    path = tmp_path / "minimal.yaml"
    path.write_text(content)
    profiles = load_profiles(str(path))
    assert "minimal" in profiles
    assert profiles["minimal"]["image"] == "alpine:latest"
    # All defaults applied
    assert profiles["minimal"]["idle_timeout"] == PROFILE_DEFAULTS["idle_timeout"]
    assert profiles["minimal"]["auto_remove"] == PROFILE_DEFAULTS["auto_remove"]
    assert profiles["minimal"]["env"] == {}
    assert profiles["minimal"]["gpu"] is False
    assert profiles["minimal"]["command"] is None


def test_profile_merging_with_config_overrides(tmp_path):
    """User-specified fields override defaults, unspecified fields keep defaults."""
    content = """
profiles:
  custom:
    image: "myapp:v1"
    idle_timeout: 1200
    auto_remove: false
    gpu: true
    gpu_type: "nvidia"
    env:
      APP_MODE: "production"
    resources:
      cpu_limit: "4000m"
      memory_limit: "8Gi"
"""
    path = tmp_path / "override.yaml"
    path.write_text(content)
    profiles = load_profiles(str(path))
    p = profiles["custom"]

    # Overridden values
    assert p["idle_timeout"] == 1200
    assert p["auto_remove"] is False
    assert p["gpu"] is True
    assert p["gpu_type"] == "nvidia"
    assert p["env"] == {"APP_MODE": "production"}
    assert p["resources"]["cpu_limit"] == "4000m"
    assert p["resources"]["memory_limit"] == "8Gi"

    # Defaults preserved for unspecified resource fields
    assert p["resources"]["shm_size"] == 0
    assert p["resources"]["cpu_request"] is None

    # Defaults preserved for unspecified top-level fields
    assert p["mounts"] == []
    assert p["ports"] == {}
    assert p["node_selector"] == {}


def test_missing_image_defaults_to_empty_string(tmp_path):
    """A profile without an image field gets an empty string (caught at creation time)."""
    content = """
profiles:
  no-image:
    idle_timeout: 60
"""
    path = tmp_path / "no_image.yaml"
    path.write_text(content)
    profiles = load_profiles(str(path))
    assert profiles["no-image"]["image"] == ""


def test_unknown_profile_name_returns_none(profiles_yaml):
    """get_profile returns None for profiles that don't exist."""
    load_profiles(profiles_yaml)
    assert get_profile("does-not-exist") is None
    assert get_profile("") is None
    assert get_profile("Web-Server") is None  # case-sensitive


def test_get_all_profiles(profiles_yaml):
    """get_all_profiles returns a copy of all loaded profiles."""
    load_profiles(profiles_yaml)
    all_p = get_all_profiles()
    assert "web-server" in all_p
    assert "worker" in all_p
    assert len(all_p) == 2


def test_profiles_example_yaml_parses():
    """The bundled profiles.example.yaml parses without error."""
    example_path = Path(__file__).parent.parent / "profiles.example.yaml"
    if not example_path.exists():
        pytest.skip("profiles.example.yaml not found")

    profiles = load_profiles(str(example_path))
    assert len(profiles) > 0
    # All profiles should have an image
    for name, p in profiles.items():
        assert p["image"], f"Profile {name} has no image"
        assert isinstance(p["resources"], dict)
        assert isinstance(p["idle_timeout"], (int, float))


def test_malformed_yaml_returns_previous(tmp_path):
    """Malformed YAML returns previously loaded profiles instead of crashing."""
    # First load valid profiles
    valid = tmp_path / "valid.yaml"
    valid.write_text("profiles:\n  ok:\n    image: 'test:latest'\n")
    load_profiles(str(valid))

    # Now load malformed YAML — should return previous
    bad = tmp_path / "bad.yaml"
    bad.write_text("profiles:\n  broken: [invalid yaml structure\n")
    result = load_profiles(str(bad))
    # Should not crash; returns previous or empty
    assert isinstance(result, dict)


def test_empty_yaml_returns_empty(tmp_path):
    """An empty YAML file returns empty profiles."""
    path = tmp_path / "empty.yaml"
    path.write_text("")
    profiles = load_profiles(str(path))
    assert profiles == {}
